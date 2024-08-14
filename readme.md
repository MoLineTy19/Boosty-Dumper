# Boosty Dumper by MoLineTy19

<!--Установка-->
## Установка
1. Клонирование репозитория
```git clone https://github.com/MoLineTy19/Boosty-Dumper```
2. Переход в директорию
```cd Boosty-Dumper```
3. Создание виртуального окружения
```python -m venv venv```
4. Активация виртуального окружения
```
[Linux] source venv/bin/activate
[Windows] venv/bin/activate
```
5. Установка зависимостей
```pip install -r requirements.txt```
6. Запуск скрипта
```python main.py```

### Получение куки (хедерсы)
Заходите на [сайт](https://boosty.to/), выбираете любого автора. Нажимаете F12 (инструменты разработчика), далее Network (соединение), ставите фильтр Fetch/XHR и перезагружаете страницу.
В поиске по запросам вписываем `media_album/?type` и копируем запрос как cURL(bash). Содержимое буфера обмена необходимо вставить на [curlconverter](https://curlconverter.com/python/) и скопировать значение переменной Headers, его необходимо вставить в файл cookies.py

# Ресурсы
- `Python 3.12`
- `Google Chrome Browser`