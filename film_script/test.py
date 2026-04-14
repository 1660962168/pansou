
from curl_cffi import requests
response = requests.get('https://www.seedhub.cc/movies/124080/', impersonate="chrome")
print(response.status_code)
print(response.text)