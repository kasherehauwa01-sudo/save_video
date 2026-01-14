import os
from io import BytesIO
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st


# Карта MIME-типов для прямых файлов
MIME_MAP = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "ts": "video/mp2t",
}


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


# Функция для загрузки HLS-плейлиста (m3u8)
def download_hls_playlist(playlist_url: str) -> Optional[bytes]:
    """Скачивает HLS-плейлист и склеивает сегменты в один файл."""
    response = requests.get(playlist_url, timeout=30)
    if response.status_code != 200:
        return None

    lines = response.text.splitlines()
    segment_urls = [
        urljoin(playlist_url, line.strip())
        for line in lines
        if line and not line.startswith("#")
    ]

    if not segment_urls:
        return None

    buffer = BytesIO()
    for segment_url in segment_urls:
        segment_response = requests.get(segment_url, stream=True, timeout=30)
        if segment_response.status_code != 200:
            return None
        for chunk in segment_response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                buffer.write(chunk)
    return buffer.getvalue()


# Функция для анализа ссылки и поиска доступных форматов
def inspect_url(url: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """Изучает ссылку и возвращает список вариантов скачивания."""
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        return [], "Не удалось открыть ссылку: сервер вернул неуспешный статус."

    content_type = response.headers.get("Content-Type", "").lower()
    is_m3u8 = "mpegurl" in content_type or url.lower().endswith(".m3u8")

    if is_m3u8:
        lines = response.text.splitlines()
        options = []
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                resolution = "неизвестно"
                for part in line.split(","):
                    if part.strip().startswith("RESOLUTION="):
                        resolution = part.split("=", 1)[1]
                if index + 1 < len(lines):
                    variant_url = urljoin(url, lines[index + 1].strip())
                    label = f"HLS {resolution}"
                    options.append(
                        {
                            "label": label,
                            "url": variant_url,
                            "extension": "ts",
                            "mime": MIME_MAP["ts"],
                            "type": "hls",
                        }
                    )

        if options:
            return options, None

        return (
            [
                {
                    "label": "HLS (без выбора качества)",
                    "url": url,
                    "extension": "ts",
                    "mime": MIME_MAP["ts"],
                    "type": "hls",
                }
            ],
            None,
        )

    parsed_url = urlparse(url)
    path_part = os.path.basename(parsed_url.path)
    extension = os.path.splitext(path_part)[1].lstrip(".") or "mp4"
    mime_type = MIME_MAP.get(extension, "video/mp4")

    return (
        [
            {
                "label": f"Оригинал ({extension})",
                "url": url,
                "extension": extension,
                "mime": mime_type,
                "type": "direct",
            }
        ],
        None,
    )


# Заголовок страницы
st.title("Скачивание видео по ссылке")

# Поле для ввода URL
url = st.text_input("Введите URL видео")

# Кнопка для проверки доступных форматов
if st.button("Проверить ссылку"):
    if not url:
        st.error("URL не должен быть пустым.")
    elif not (url.startswith("http://") or url.startswith("https://")):
        st.warning("URL должен начинаться с http:// или https://")
    else:
        try:
            options, error_message = inspect_url(url)
            if error_message:
                st.error(error_message)
            elif not options:
                st.error("Не удалось определить доступные форматы.")
            else:
                st.session_state["download_options"] = options
                st.success("Форматы определены. Выберите подходящий вариант.")
        except requests.RequestException as exc:
            st.error(f"Ошибка при проверке ссылки: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Непредвиденная ошибка: {exc}")

# Выпадающий список с доступными форматами
options = st.session_state.get("download_options", [])
option_labels = [option["label"] for option in options]
if option_labels:
    selected_label = st.selectbox("Выберите формат/разрешение", option_labels)
else:
    selected_label = st.selectbox(
        "Выберите формат/разрешение", ["Сначала проверьте ссылку"], disabled=True
    )

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
            if not options:
                st.warning("Сначала нажмите «Проверить ссылку», чтобы выбрать формат.")
            else:
                selected_option = next(
                    (option for option in options if option["label"] == selected_label),
                    None,
                )
                if not selected_option:
                    st.error("Не удалось определить выбранный формат.")
                    st.stop()

                # Пытаемся скачать файл
                if selected_option["type"] == "hls":
                    data = download_hls_playlist(selected_option["url"])
                else:
                    data = download_file(selected_option["url"])

                if data is None:
                    st.error("Не удалось скачать файл: сервер вернул неуспешный статус.")
                elif data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    st.error(
                        "Ссылка вернула HTML-страницу, а не видео. "
                        "Проверьте прямую ссылку на файл."
                    )
                else:
                    # Определяем имя файла на основе URL
                    parsed_url = urlparse(url)
                    path_part = os.path.basename(parsed_url.path)
                    base_name = os.path.splitext(path_part)[0] or "video"

                    file_name = (
                        f"{base_name}.{selected_option['extension']}"
                    )

                    # Рассчитываем размер в мегабайтах
                    size_mb = len(data) / (1024 * 1024)
                    st.success(
                        f"Файл загружен. Примерный размер: {size_mb:.2f} МБ."
                    )

                    # Кнопка скачивания
                    st.download_button(
                        label="Скачать файл",
                        data=data,
                        file_name=file_name,
                        mime=selected_option["mime"],
                    )
        except requests.RequestException as exc:
            st.error(f"Ошибка при загрузке файла: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Непредвиденная ошибка: {exc}")
