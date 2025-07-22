
from instagrapi import Client

BOT_USERNAME = "botkullaniciadi"
BOT_PASSWORD = "botsifre123"

def login_bot(username, password):
    cl = Client()
    cl.login(username, password)
    return cl

def follow_user(client, username):
    user_id = client.user_id_from_username(username)
    client.user_follow(user_id)
