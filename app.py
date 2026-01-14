import os
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import requests
import streamlit as st


# Функция для загрузки файла по URL
def download_file(url: str) -> Optional[bytes]:
    """Скачивает файл по прямой ссылке и возвращает байты."""
    response = requests.get(url, stream=True, timeout=30)
    if response.status_code != 200:
        return None

    # Собираем содержимое в память
    buffer = BytesIO()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buffer.write(chunk)
    return buffer.getvalue()


# Заголовок страницы
st.title("Скачивание видео по ссылке")

# Поле для ввода URL
url = st.text_input("Введите URL видео")

# Выбор расширения файла
extension = st.selectbox("Выберите расширение файла", ["mp4", "mov", "avi", "mkv"])

# Кнопка для запуска скачивания
if st.button("Скачать видео"):
    # Проверка на пустой URL
    if not url:
        st.error("URL не должен быть пустым.")
    # Простая проверка на корректный протокол
    elif not (url.startswith("http://") or url.startswith("https://")):
        st.warning("URL должен начинаться с http:// или https://")
    else:
        try:
            # Пытаемся скачать файл
            data = download_file(url)
            if data is None:
                st.error("Не удалось скачать файл: сервер вернул неуспешный статус.")
            else:
                # Определяем имя файла на основе URL
                parsed_url = urlparse(url)
                path_part = os.path.basename(parsed_url.path)
                base_name = os.path.splitext(path_part)[0] or "video"

                file_name = f"{base_name}.{extension}"

                # Рассчитываем размер в мегабайтах
                size_mb = len(data) / (1024 * 1024)
                st.success(
                    f"Файл загружен. Примерный размер: {size_mb:.2f} МБ."
                )

                # Подбираем MIME-тип по расширению
                mime_map = {
                    "mp4": "video/mp4",
                    "mov": "video/quicktime",
                    "avi": "video/x-msvideo",
                    "mkv": "video/x-matroska",
                }
                mime_type = mime_map.get(extension, "video/mp4")

                # Кнопка скачивания
                st.download_button(
                    label="Скачать файл",
                    data=data,
                    file_name=file_name,
                    mime=mime_type,
                )
        except requests.RequestException as exc:
            st.error(f"Ошибка при загрузке файла: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Непредвиденная ошибка: {exc}")
