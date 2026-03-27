import os
from dotenv import load_dotenv

load_dotenv()

LOGIN_INFO = {
    "login_url": os.environ.get("GROUPWARE_DOMAIN"),
    "username": os.environ.get("LOGIN_ID"),
    "password": os.environ.get("LOGIN_PW"),
}