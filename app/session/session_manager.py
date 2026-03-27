session_store = {}

def get_session(username: str):
    return session_store.get(username)

def save_session(username: str, cookies):
    session_store[username] = cookies