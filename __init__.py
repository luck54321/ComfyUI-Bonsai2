import base64
import io as bytes_io
import json
import queue
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

import comfy.model_management
import comfy.utils
import folder_paths
from comfy_api.latest import ComfyExtension, io, ui

ROOT = Path(__file__).resolve().parent
MODEL_DIR = Path(folder_paths.models_dir) / 'LLM' / 'Bonsai-2-27B'
MODEL_FILES = (
    'Ternary-Bonsai-2-27B-PTQ1_0.gguf',
    'Ternary-Bonsai-2-27B-PQ2_0.gguf',
    'Ternary-Bonsai-2-27B-Q2_0.gguf',
)
PROJECTOR = 'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf'
CONFIG = ROOT / 'model_paths.json'
if CONFIG.is_file():
    folders = json.loads(CONFIG.read_text(encoding='utf-8'))['model_folders']
    if not isinstance(folders, list) or not all(isinstance(folder, str) and Path(folder).is_absolute() for folder in folders):
        raise ValueError('model_paths.json must contain a model_folders list of absolute folder paths.')
    for folder in folders:
        folder_paths.add_model_folder_path('bonsai2', folder)
folder_paths.add_model_folder_path('bonsai2', str(MODEL_DIR))


def resolve_model(name):
    if name not in MODEL_FILES and name != PROJECTOR:
        raise ValueError('Unsupported Bonsai model filename.')
    for folder in folder_paths.get_folder_paths('bonsai2'):
        root = Path(folder).resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Model path must stay in a configured Bonsai model folder.')
        if path.is_file():
            return path
    raise FileNotFoundError(f'Missing {name}. Check model_paths.json or run install_windows.ps1 in {ROOT}.')


def image_content(images, prompt):
    content = [{'type': 'text', 'text': prompt}]
    for tensor in images:
        pixels = np.clip(tensor.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
        image = Image.fromarray(pixels).convert('RGB')
        buffer = bytes_io.BytesIO()
        image.save(buffer, format='PNG', compress_level=1)
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{encoded}'}})
    return content


class LocalServer:
    def __init__(self, model, context_size, gpu_layers, with_images, image_max_tokens=0):
        self.process = None
        self.log = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.api_key = secrets.token_hex(24)
        self.session.headers['Authorization'] = f'Bearer {self.api_key}'
        self.model = model
        self.context_size = context_size
        self.gpu_layers = gpu_layers
        self.with_images = with_images
        self.image_max_tokens = image_max_tokens
        self.log_path = ROOT / 'runtime' / f'server-{secrets.token_hex(6)}.log'

    def start(self):
        binary = ROOT / 'runtime' / 'llama-server.exe'
        if not binary.is_file():
            raise FileNotFoundError(f'Missing Bonsai runtime. Run install_windows.ps1 in {ROOT}.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        command = [
            str(binary), '-m', str(self.model), '--alias', 'bonsai2',
            '--host', '127.0.0.1', '--port', str(port), '--api-key', self.api_key,
            '-c', str(self.context_size),
            '-np', '1', '-fa', 'on', '--jinja',
            '--batch-size', '256', '--ubatch-size', '128', '--no-warmup',
        ]
        if self.gpu_layers >= 0:
            command.extend(['-ngl', str(self.gpu_layers)])
        else:
            command.extend(['--fit', 'on'])
        if self.with_images:
            command.extend(['--mmproj', str(resolve_model(PROJECTOR))])
            if self.gpu_layers == 0:
                command.append('--no-mmproj-offload')
            if self.image_max_tokens > 0:
                command.extend(['--image-max-tokens', str(self.image_max_tokens)])
        self.log = self.log_path.open('w', encoding='utf-8')
        self.process = subprocess.Popen(
            command, cwd=binary.parent, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            comfy.model_management.throw_exception_if_processing_interrupted()
            if self.process.poll() is not None:
                raise RuntimeError(f'Bonsai failed to load. Free GPU memory or lower gpu_layers. Log: {self.log_path}\n{self.log_tail()}')
            try:
                response = self.session.get(self.url + '/v1/models', timeout=1)
                if response.status_code == 200:
                    return
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(0.25)
        raise TimeoutError(f'Bonsai model startup timed out. See {self.log_path}.')

    def log_tail(self):
        if self.log:
            self.log.flush()
        if self.log_path.exists():
            with self.log_path.open('rb') as stream:
                stream.seek(max(0, self.log_path.stat().st_size - 4000))
                return stream.read().decode('utf-8', errors='replace')
        return ''

    def generate(self, payload):
        results = queue.Queue()
        progress = comfy.utils.ProgressBar(payload['max_tokens'])
        progress.update_absolute(0)
        next_progress = time.monotonic()

        def request():
            try:
                response = self.session.post(self.url + '/v1/chat/completions', json=payload, timeout=(5, 900))
                response.raise_for_status()
                results.put((response.json(), None))
            except (requests.RequestException, ValueError) as error:
                results.put((None, error))

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                comfy.model_management.throw_exception_if_processing_interrupted()
                worker.join(0.2)
                if time.monotonic() >= next_progress:
                    counts = re.findall(r'\bn_gen\s*=\s*(\d+)', self.log_tail())
                    if counts:
                        progress.update_absolute(int(counts[-1]))
                    next_progress = time.monotonic() + 1
            result, error = results.get_nowait()
            if error:
                raise RuntimeError(f'Bonsai request failed: {error}\n{self.log_tail()}') from error
            if 'error' in result:
                raise RuntimeError(f'Bonsai: {result}')
            progress.update_absolute(payload['max_tokens'])
            return result
        finally:
            if worker.is_alive():
                self.stop()
                worker.join(timeout=5)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.session.close()
        if self.log:
            self.log.close()


class Bonsai2Generate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        installed_models = []
        for name in MODEL_FILES:
            try:
                resolve_model(name)
            except FileNotFoundError:
                continue
            installed_models.append(name)
        models = installed_models or [MODEL_FILES[0]]
        return io.Schema(
            node_id='Bonsai2Generate', display_name='Bonsai 2 27B - Local Chat',
            category='Bonsai 2', is_output_node=True,
            description='Local Bonsai 2 inference. Model memory is released after every run. Optional image batches are sent as multiple images in one request.',
            inputs=[
                io.Combo.Input('model', options=models, default=models[0]),
                io.String.Input('prompt', multiline=True, default='Please answer in Chinese: What can you help me with?'),
                io.String.Input('system_prompt', multiline=True, default='You are a helpful assistant.'),
                io.Boolean.Input('thinking', default=False),
                io.Int.Input('max_tokens', default=2048, min=1, max=65536),
                io.Int.Input('context_size', default=4096, min=512, max=262144, step=512),
                io.Int.Input('gpu_layers', default=-1, min=-1, max=99, tooltip='-1: automatically fit layers to available GPU memory. 99: full GPU. 0: CPU. Existing values such as 24 remain manual.'),
                io.Float.Input('temperature', default=0.7, min=0.0, max=2.0, step=0.05),
                io.Int.Input('seed', default=0, min=0, max=2147483647, control_after_generate=True),
                io.Image.Input('image', optional=True),
                io.Boolean.Input('release_comfy_models', default=True, optional=True, tooltip='Unload ComfyUI models from GPU before Bonsai. Downstream image models reload when needed. Does not stop other applications.'),
                io.Int.Input('image_max_tokens', default=0, min=0, max=16384, step=256, optional=True, tooltip='0: preserve runtime image detail defaults. 1024: faster image processing with less detail; may reduce small-text and fine-detail accuracy.'),
            ],
            outputs=[io.String.Output(display_name='text'), io.String.Output(display_name='reasoning')],
        )

    @classmethod
    def execute(cls, model, prompt, system_prompt, thinking, max_tokens, context_size, gpu_layers, temperature, seed, image=None, release_comfy_models=True, image_max_tokens=0):
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt if image is None else image_content(image, prompt)})
        payload = {
            'model': 'bonsai2', 'messages': messages, 'stream': False,
            'max_tokens': max_tokens, 'temperature': temperature, 'seed': seed,
            'top_p': 0.95 if thinking else 0.8, 'top_k': 20,
            'min_p': 0.05 if thinking else 0.0, 'presence_penalty': 0.0 if thinking else 1.5,
            'repeat_penalty': 1.0,
            'chat_template_kwargs': {'enable_thinking': thinking},
            'reasoning_format': 'deepseek',
        }
        if thinking:
            payload['reasoning_effort'] = 'medium'
        server = LocalServer(resolve_model(model), context_size, gpu_layers, image is not None, image_max_tokens)
        keep_log = True
        try:
            if release_comfy_models and gpu_layers != 0:
                comfy.model_management.unload_all_models()
                comfy.model_management.soft_empty_cache()
            server.start()
            result = server.generate(payload)
            choice = result['choices'][0]
            message = choice['message']
            text = message.get('content') or ''
            reasoning = message.get('reasoning_content') or ''
            if not text and choice.get('finish_reason') == 'length':
                raise RuntimeError('Bonsai used the token budget before answering. Increase max_tokens or turn thinking off.')
            output = io.NodeOutput(text, reasoning, ui=ui.PreviewText(text))
            keep_log = False
            return output
        except comfy.model_management.InterruptProcessingException:
            keep_log = False
            raise
        finally:
            server.stop()
            if not keep_log:
                server.log_path.unlink(missing_ok=True)


class BonsaiExtension(ComfyExtension):
    async def get_node_list(self):
        return [Bonsai2Generate]


async def comfy_entrypoint():
    return BonsaiExtension()
