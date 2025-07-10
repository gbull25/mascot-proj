import asyncio
import os
import json
import logging
from typing import List, Dict, Any, Optional
from PIL import Image
from io import BytesIO
import httpx
import random
import time

COMFYUI_URL = "http://127.0.0.1:8188"
OUTPUT_NODE_ID = "9"
CLIENT_ID = "mascot_app"
DEFAULT_WORKFLOW_PATH = "/home/ubuntu/mascot-project/workflows/node1.json"
DEFAULT_MASCOT_DIR = "/home/ubuntu/mascot-project/mascot-imgs"

logger = logging.getLogger("ComfyUIClient")
logging.basicConfig(level=logging.INFO)

class ComfyUIClient:
    """
    Асинхронный клиент для взаимодействия с сервером ComfyUI.
    Позволяет загружать изображения, отправлять воркфлоу и получать результаты генерации.
    """
    def __init__(
        self,
        base_url: str = COMFYUI_URL,
        client_id: str = CLIENT_ID,
        workflow_path: str = DEFAULT_WORKFLOW_PATH,
        mascot_dir: str = DEFAULT_MASCOT_DIR,
        timeout: int = 300,
    ):
        self.base_url = base_url.rstrip('/')
        self.client_id = client_id
        self.workflow_path = workflow_path
        self.mascot_dir = mascot_dir
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def check_server_status(self) -> bool:
        """
        Проверяет доступность сервера ComfyUI.
        """
        try:
            response = await self._client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            logger.info("Сервер ComfyUI доступен")
            return True
        except Exception as e:
            logger.error(f"Сервер ComfyUI недоступен: {e}")
            return False

    async def upload_image(self, image_path: str) -> str:
        """
        Асинхронно загружает изображение на сервер ComfyUI.
        """
        url = f"{self.base_url}/upload/image"
        filename = os.path.basename(image_path)
        try:
            with open(image_path, 'rb') as img_file:
                files = {'image': (filename, img_file, 'application/octet-stream')}
                data = {'type': "input", 'overwrite': "true"}
                response = await self._client.post(url, files=files, data=data)
            response.raise_for_status()
            logger.info(f"Изображение {filename} успешно загружено.")
            return filename
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения {filename}: {e}")
            raise

    async def queue_prompt(self, prompt_json: Dict[str, Any]) -> str:
        """
        Асинхронно отправляет воркфлоу (prompt) на сервер ComfyUI.
        """
        url = f"{self.base_url}/prompt"
        payload = {"prompt": prompt_json, "client_id": self.client_id}
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            prompt_id = response.json()['prompt_id']
            logger.info(f"Prompt отправлен, ID: {prompt_id}")
            return prompt_id
        except Exception as e:
            logger.error(f"Не удалось отправить промпт: {e}")
            raise

    async def wait_for_completion(self, prompt_id: str, timeout: int = None) -> Dict[str, Any]:
        """
        Асинхронно ожидает завершения выполнения воркфлоу.
        """
        if timeout is None:
            timeout = self.timeout
            
        url = f"{self.base_url}/history/{prompt_id}"
        start_time = time.time()
        check_interval = 2
        
        while True:
            if time.time() - start_time > timeout:
                logger.error("Превышено время ожидания ответа от ComfyUI")
                raise TimeoutError("Превышено время ожидания ответа от ComfyUI")
            
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                history = response.json()
                
                if prompt_id in history:
                    status = history[prompt_id].get("status", {})
                    if status.get("completed"):
                        logger.info("Генерация завершена")
                        return history[prompt_id]
                    elif status.get("error"):
                        error_msg = status.get("error", "Неизвестная ошибка")
                        logger.error(f"Ошибка генерации: {error_msg}")
                        raise RuntimeError(f"Ошибка генерации: {error_msg}")
                        
            except httpx.RequestError as e:
                logger.warning(f"Ошибка сети при проверке статуса: {e}")
            except Exception as e:
                logger.warning(f"Ошибка при проверке статуса: {e}")
                
            await asyncio.sleep(check_interval)
            
            # Увеличиваем интервал проверки для снижения нагрузки на сервер
            if check_interval < 10:
                check_interval = min(check_interval + 0.5, 10)

    async def get_output_images(self, history_data: Dict[str, Any], output_node: str = OUTPUT_NODE_ID) -> List[str]:
        """
        Возвращает пути к сгенерированным изображениям на диске.
        """
        try:
            images_info = history_data["outputs"][output_node]["images"]
        except KeyError as e:
            logger.error(f"Не удалось найти изображения в выводе: {e}")
            logger.error(f"Доступные узлы: {list(history_data.get('outputs', {}).keys())}")
            return []
            
        # Получаем filename_prefix из workflow
        try:
            prefix = history_data.get('inputs', {}).get(output_node, {}).get('filename_prefix')
            prefix = prefix if prefix else 'Kontext/'
        except Exception:
            prefix = 'Kontext/'
        
        results = []
        for image in images_info:
            filename = image['filename']
            # Строим полный путь к файлу
            if '/' not in filename and prefix:
                file_path = os.path.join('/home/ubuntu', 'mascot-project', 'ComfyUI', 'output', prefix, filename)
            else:
                file_path = os.path.join('/home/ubuntu', 'mascot-project', 'ComfyUI', 'output', filename)
            results.append(file_path)
            
        return results

    async def aclose(self):
        await self._client.aclose()

    @staticmethod
    async def generate_with_comfyui(
        prompt: str,
        user_image_path: str,
        workflow_path: str = DEFAULT_WORKFLOW_PATH,
        mascot_dir: str = DEFAULT_MASCOT_DIR,
        base_url: str = COMFYUI_URL,
        client_id: str = CLIENT_ID,
        timeout: int = 300,
    ) -> Optional[str]:
        """
        Полный цикл генерации изображения через ComfyUI.
        Возвращает путь к сгенерированному изображению или None в случае ошибки.
        """
        async with ComfyUIClient(
            base_url=base_url, 
            client_id=client_id, 
            workflow_path=workflow_path, 
            mascot_dir=mascot_dir,
            timeout=timeout,
        ) as client:
            try:
                # Проверяем доступность сервера
                if not await client.check_server_status():
                    logger.error("Сервер ComfyUI недоступен")
                    return None
                
                # Загружаем изображение пользователя
                user_image_filename = await client.upload_image(user_image_path)
                
                # Выбираем случайное изображение маскота
                mascot_filename = await client._upload_random_mascot()
                if not mascot_filename:
                    return None
                
                # Загружаем и модифицируем воркфлоу
                workflow = await client._load_and_modify_workflow(prompt, user_image_filename, mascot_filename)
                if not workflow:
                    return None
                
                # Отправляем воркфлоу и ждем завершения
                prompt_id = await client.queue_prompt(workflow)
                history = await client.wait_for_completion(prompt_id, timeout=timeout)
                result_images = await client.get_output_images(history)
                
                if result_images:
                    logger.info(f"Генерация завершена. Получено {len(result_images)} изображений.")
                    return result_images[0]
                else:
                    logger.error("Генерация не вернула изображений.")
                    return None
                    
            except TimeoutError:
                logger.error("Превышено время ожидания генерации")
                return None
            except Exception as e:
                logger.error(f"Ошибка генерации: {e}")
                return None

    async def _upload_random_mascot(self) -> Optional[str]:
        """
        Загружает случайное изображение маскота.
        """
        if not os.path.exists(self.mascot_dir):
            logger.error(f"Директория маскота не существует: {self.mascot_dir}")
            return None
            
        mascots = [f for f in os.listdir(self.mascot_dir) 
                   if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not mascots:
            logger.error("Нет фото маскота в директории.")
            return None
            
        mascot_path = os.path.join(self.mascot_dir, random.choice(mascots))
        return await self.upload_image(mascot_path)

    async def _load_and_modify_workflow(self, prompt: str, user_image_filename: str, mascot_filename: str) -> Optional[Dict[str, Any]]:
        """
        Загружает воркфлоу и модифицирует его с пользовательскими данными.
        """
        if not os.path.exists(self.workflow_path):
            logger.error(f"Файл воркфлоу не существует: {self.workflow_path}")
            return None
            
        try:
            with open(self.workflow_path, "r", encoding="utf-8") as f:
                workflow = json.load(f)
            
            # Обновляем воркфлоу с пользовательскими данными
            workflow["85"]["inputs"]["image"] = user_image_filename
            workflow["41"]["inputs"]["image"] = mascot_filename
            workflow["6"]["inputs"]["text"] = prompt
            workflow["25"]["inputs"]["noise_seed"] = random.randint(0, 2**63 - 1)
            
            return workflow
        except Exception as e:
            logger.error(f"Ошибка при загрузке/модификации воркфлоу: {e}")
            return None