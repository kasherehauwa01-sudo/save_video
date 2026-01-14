import os
from io import BytesIO
import importlib.util
import re
import tempfile
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


# Функция для поиска ссылок на видео в HTML-странице
def extract_video_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Ищет ссылки на видео в HTML и возвращает список вариантов."""
    candidates: set[str] = set()

    # Ищем теги <video src="..."> и <source src="...">
    for match in re.findall(r"""(?:video|source)[^>]+src=["']([^"']+)["']""", html):
        candidates.add(urljoin(base_url, match))

    # Ищем прямые ссылки в <a href="...">
    for match in re.findall(r"""<a[^>]+href=["']([^"']+)["']""", html):
        candidates.add(urljoin(base_url, match))

    # Отбираем ссылки по расширениям
    allowed_ext = {"mp4", "mov", "avi", "mkv", "m3u8", "ts"}
    options: list[dict[str, str]] = []
    for link in sorted(candidates):
        parsed = urlparse(link)
        ext = os.path.splitext(parsed.path)[1].lstrip(".").lower()
        if ext in allowed_ext:
            if ext == "m3u8":
                label = "HLS (m3u8)"
                options.append(
                    {
                        "label": label,
                        "url": link,
                        "extension": "ts",
                        "mime": MIME_MAP["ts"],
                        "type": "hls",
                    }
                )
            else:
                label = f"Видео ({ext})"
                options.append(
                    {
                        "label": label,
                        "url": link,
                        "extension": ext,
                        "mime": MIME_MAP.get(ext, "video/mp4"),
                        "type": "direct",
                    }
                )

    return options


# Функция для получения доступных форматов через yt-dlp
def get_ytdlp_options(url: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """Получает список форматов через yt-dlp, если он установлен."""
    if importlib.util.find_spec("yt_dlp") is None:
        return [], "yt-dlp не установлен. Установите пакет для использования режима."

    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats", [])
    options: list[dict[str, str]] = []
    for fmt in formats:
        if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
            resolution = fmt.get("resolution") or fmt.get("format_note") or "неизвестно"
            label = f"{fmt.get('format_id')} | {fmt.get('ext')} | {resolution}"
            options.append(
                {
                    "label": label,
                    "url": url,
                    "extension": fmt.get("ext") or "mp4",
                    "mime": MIME_MAP.get(fmt.get("ext") or "mp4", "video/mp4"),
                    "type": "ytdlp",
                    "format_id": fmt.get("format_id"),
                }
            )

    if not options:
        return (
            [],
            "yt-dlp не нашел форматы с видео и аудио в одном файле. "
            "Для объединения потоков обычно нужен ffmpeg.",
        )

    return options, None


# Функция для скачивания через yt-dlp
def download_with_ytdlp(url: str, format_id: str) -> tuple[Optional[bytes], Optional[str]]:
    """Скачивает файл через yt-dlp и возвращает байты и имя файла."""
    if importlib.util.find_spec("yt_dlp") is None:
        return None, None

    import yt_dlp

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": output_template,
            "format": format_id,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        if not os.path.exists(file_path):
            files = os.listdir(tmpdir)
            if not files:
                return None, None
            file_path = os.path.join(tmpdir, files[0])

        with open(file_path, "rb") as downloaded_file:
            data = downloaded_file.read()

        return data, os.path.basename(file_path)


# Функция для анализа ссылки и поиска доступных форматов
def inspect_url(url: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """Изучает ссылку и возвращает список вариантов скачивания."""
    head_response = requests.head(url, allow_redirects=True, timeout=30)
    if head_response.status_code not in (200, 206, 405):
        return [], "Не удалось открыть ссылку: сервер вернул неуспешный статус."

    if head_response.status_code == 405:
        head_response = requests.get(url, stream=True, timeout=30)
        if head_response.status_code != 200:
            return [], "Не удалось открыть ссылку: сервер вернул неуспешный статус."

    content_type = head_response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type:
        page_response = requests.get(url, timeout=30)
        if page_response.status_code != 200:
            return (
                [],
                "Ссылка ведет на HTML-страницу, но страницу не удалось открыть.",
            )

        options = extract_video_links(page_response.text, url)
        if not options:
            return (
                [],
                "На странице не найдено прямых ссылок на видео или плейлист. "
                "Такое бывает, если видео подгружается скриптами, "
                "используется blob: URL или требуется авторизация.",
            )

        return options, None

    is_m3u8 = "mpegurl" in content_type or url.lower().endswith(".m3u8")

    if is_m3u8:
        playlist_response = requests.get(url, timeout=30)
        if playlist_response.status_code != 200:
            return [], "Не удалось открыть плейлист: сервер вернул неуспешный статус."

        lines = playlist_response.text.splitlines()
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

# Инициализация логов
if "logs" not in st.session_state:
    st.session_state["logs"] = []


# Функция для добавления сообщений в лог
def add_log(message: str) -> None:
    """Добавляет сообщение в лог для отображения в интерфейсе."""
    st.session_state["logs"].append(message)


# Поле для ввода URL
url = st.text_input("Введите URL видео")

# Опциональный режим для yt-dlp
use_ytdlp = st.checkbox("Использовать yt-dlp (YouTube и сложные сайты)")

# Кнопка для проверки доступных форматов
if st.button("Проверить ссылку"):
    if not url:
        add_log("Проверка ссылки: URL пустой.")
        st.error("URL не должен быть пустым.")
    elif not (url.startswith("http://") or url.startswith("https://")):
        add_log("Проверка ссылки: неверный протокол.")
        st.warning("URL должен начинаться с http:// или https://")
    else:
        try:
            add_log("Проверка ссылки: начинаем анализ URL.")
            if use_ytdlp:
                add_log("Проверка ссылки: используем yt-dlp.")
                options, error_message = get_ytdlp_options(url)
            else:
                options, error_message = inspect_url(url)
            if error_message:
                add_log(f"Проверка ссылки: ошибка - {error_message}")
                st.error(error_message)
            elif not options:
                add_log("Проверка ссылки: варианты не найдены.")
                st.error("Не удалось определить доступные форматы.")
            else:
                st.session_state["download_options"] = options
                add_log(f"Проверка ссылки: найдено вариантов - {len(options)}.")
                st.success("Форматы определены. Выберите подходящий вариант.")
        except requests.RequestException as exc:
            add_log(f"Проверка ссылки: ошибка запроса - {exc}")
            st.error(f"Ошибка при проверке ссылки: {exc}")
        except Exception as exc:  # noqa: BLE001
            add_log(f"Проверка ссылки: непредвиденная ошибка - {exc}")
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
        add_log("Скачивание: URL пустой.")
        st.error("URL не должен быть пустым.")
    # Простая проверка на корректный протокол
    elif not (url.startswith("http://") or url.startswith("https://")):
        add_log("Скачивание: неверный протокол URL.")
        st.warning("URL должен начинаться с http:// или https://")
    else:
        try:
            if not options:
                add_log("Скачивание: форматы не выбраны.")
                st.warning("Сначала нажмите «Проверить ссылку», чтобы выбрать формат.")
            else:
                selected_option = next(
                    (option for option in options if option["label"] == selected_label),
                    None,
                )
                if not selected_option:
                    add_log("Скачивание: выбранный формат не найден.")
                    st.error("Не удалось определить выбранный формат.")
                    st.stop()

                # Пытаемся скачать файл
                add_log(
                    f"Скачивание: выбран формат {selected_option['label']}."
                )
                if selected_option["type"] == "ytdlp":
                    data, ytdlp_name = download_with_ytdlp(
                        selected_option["url"],
                        selected_option.get("format_id", "best"),
                    )
                    if ytdlp_name:
                        base_name = os.path.splitext(ytdlp_name)[0] or "video"
                    else:
                        base_name = "video"
                elif selected_option["type"] == "hls":
                    data = download_hls_playlist(selected_option["url"])
                    base_name = None
                else:
                    data = download_file(selected_option["url"])
                    base_name = None

                if data is None:
                    add_log("Скачивание: сервер вернул неуспешный статус.")
                    st.error("Не удалось скачать файл: сервер вернул неуспешный статус.")
                elif data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    add_log("Скачивание: получена HTML-страница вместо видео.")
                    st.error(
                        "Ссылка вернула HTML-страницу, а не видео. "
                        "Проверьте прямую ссылку на файл."
                    )
                else:
                    # Определяем имя файла на основе URL
                    if not base_name:
                        parsed_url = urlparse(url)
                        path_part = os.path.basename(parsed_url.path)
                        base_name = os.path.splitext(path_part)[0] or "video"

                    file_name = (
                        f"{base_name}.{selected_option['extension']}"
                    )

                    # Рассчитываем размер в мегабайтах
                    size_mb = len(data) / (1024 * 1024)
                    add_log(
                        f"Скачивание: файл загружен, размер {size_mb:.2f} МБ."
                    )
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
            add_log(f"Скачивание: ошибка запроса - {exc}")
            st.error(f"Ошибка при загрузке файла: {exc}")
        except Exception as exc:  # noqa: BLE001
            add_log(f"Скачивание: непредвиденная ошибка - {exc}")
            st.error(f"Непредвиденная ошибка: {exc}")

# Блок вывода логов
st.subheader("Логи")
if st.session_state["logs"]:
    st.text("\n".join(st.session_state["logs"]))
else:
    st.caption("Логи пока пустые.")
