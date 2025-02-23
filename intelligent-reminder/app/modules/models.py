from pydantic import BaseModel

class CallRequest(BaseModel):
    first_message: str
    time: str
    event_type: str
    event_name: str
    calling_to: str
    phone_number: str
    agent_id: str
    prompt: str