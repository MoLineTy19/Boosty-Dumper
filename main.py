import json
import os.path

import requests
import shutil
from cookies import headers


def download_image(search_author: str, image_id: str) -> None:
    r = requests.get(f'https://images.boosty.to/image/{image_id}')
    path = search_author + '/images/' + image_id.split("-")[0] + '.jpg'
    with open(path, 'wb') as f:
        r.raw.decode_content = True
        shutil.copyfileobj(r.raw, f)
        f.write(r.content)


def download_video(search_author: str, data: json, headers: dict) -> None:
    res = requests.get(search_data_video(data), headers=headers, stream=True)
    path = search_author + '/videos/' + data['id'].split("-")[0] + '.mp4'
    with open(path, 'wb') as f:
        for chunk in res.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)


def get_count_media(headers: dict, search_author: str) -> [dict, int]:
    params = {
        'only_allowed': 'true',
    }
    response = requests.get(f'https://api.boosty.to/v1/blog/{search_author}/media_album/counters/',
                            params=params, headers=headers)

    if response.status_code == 401:
        print("Куки не валидны")
        exit()

    data_json = response.json()
    data = data_json['data']['mediaCounters']
    count_media = int(data['audioFile']) + int(data['okVideo']) + int(data['image'])
    print("Найдено медиа - ", count_media)
    print("Видео - ", data['okVideo'])
    print("Изображения - ", data['image'])
    print("Аудио - ", data['audioFile'], " (пока недоступно)")

    return data, count_media


def search_data_video(data_json: json):
    for url in data_json['playerUrls']:
        if url['type'] == 'high' and url['url'] != '':
            return url['url']
    for url in data_json['playerUrls']:
        if url['low'] == 'low':
            return url['url']


def get_all_links_by_account(count_media: int, headers: dict, search_author: str) -> json:
    params = {
        'type': 'all',
        'limit': f'{count_media}',
        'limit_by': 'media',
        'only_allowed': 'true',
    }

    response = requests.get(
        f'https://api.boosty.to/v1/blog/{search_author}/media_album/',
        params=params,
        headers=headers,
    )

    return response.json()['data']['mediaPosts']


def download_data(data_json: json, headers: dict, image_d: bool, video_d: bool, search_author: str) -> None:
    for data in data_json:
        all_images = data['media']
        for i, media in enumerate(all_images):
            if media['type'] == 'image' and image_d == True:
                download_image(search_author, media['id'])
            if media['type'] == 'ok_video' and video_d == True:
                download_video(search_author, media, headers)
            print()


def info_by_account(headers: dict):
    search_author = input("Укажите имя аккаунта для получения постов: \n")
    data_author = get_count_media(headers, search_author)
    if data_author[1] < 0:
        print("\nДоступных данных не найдено.")
    if not os.path.isdir(search_author):
        os.mkdir(search_author)

    action = input('\nВпишите что вам нужно:\n1. Видео\n2. Изображения\n3. Видео + изображения\n')

    video_d = False
    image_d = True

    if action == '1':
        video_d = True
        image_d = False
    elif action == '2':
        video_d = False
        image_d = True
    elif action == '3':
        video_d = True
        image_d = True
    else:
        print("[ОШИБКА] Не правильный тип передаваемых данных.")

    if not os.path.isdir(search_author + '/images') and image_d == True:
        os.mkdir(search_author + '/images')

    if not os.path.isdir(search_author + '/videos') and video_d == True:
        os.mkdir(search_author + '/videos')

    data_json = get_all_links_by_account(data_author[1], headers, search_author)
    download_data(data_json, headers, image_d, video_d, search_author)


if __name__ == '__main__':
    print("[INFO] Скрипт запущен.")
    info_by_account(headers)
