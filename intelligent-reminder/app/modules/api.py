from fastapi import APIRouter, BackgroundTasks, status, Request, Response, HTTPException, Query, WebSocket
from app.modules.models import CallRequest
from app.modules.services import ReminderService
from logger import logger
from typing import Dict
import json
import asyncio
import websockets
import websockets.exceptions

router = APIRouter()

@router.post("/calls", status_code=status.HTTP_201_CREATED)
async def create_call(
    call_request: CallRequest,
    background_tasks: BackgroundTasks,
    request: Request
):
    host = request.headers.get("host")
    background_tasks.add_task(ReminderService.create_call, call_request, host)
    return {"message": "Reminder task created successfully"}
    
@router.get("/aicalling/records/{country_code}/{agent_id}/{date}")
async def get_ai_calling_records(country_code: str, date: str, agent_id: str) -> list:
    """
    Get AI calling records for a specific country code and date
    Args:
        country_code: Country code (e.g., 'SG')
        date: Date in YYYY-MM-DD format
    """
    try:
        stream_service = ReminderService()
        records = stream_service.get_ai_calling_records_service(country_code, date, agent_id)
        return records
    except Exception as error:
        logger.error("Error fetching AI calling records: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch records") from error

@router.post("/elevenlabs/callback/outbound-call-status")
async def outbound_call_status(request: Request):
    try:
        # Log the raw request details for debugging
        logger.info("Received callback request from Twilio")
        logger.info("Headers: %s", dict(request.headers))
        
        # Get agent_id from query parameters
        agent_id = request.query_params.get("agent_id")
        logger.info("Agent ID from query params: %s", agent_id)
        
        # Parse the form data from Twilio's request
        form_data = await request.form()
        logger.info("Form data: %s", dict(form_data))
        
        to_number = form_data.get("To")
        status = form_data.get("CallStatus")
        country_code = form_data.get("ToCountry")
        logger.info("Country code from callback: %s", country_code)
        
        if not to_number or not status or not country_code:
            logger.error(
                "Missing required parameters: To=%s, CallStatus=%s, ToCountry=%s", 
                to_number, status, country_code
            )
            return Response(status_code=400, content="Missing required parameters")
            
        logger.info("Call status update for %s: %s", to_number, status)
        stream_service = ReminderService()
        
        await stream_service.handle_call_status(to_number, status, agent_id)
        return Response(content="OK", status_code=200)
        
    except (ValueError, RuntimeError, ConnectionError) as error:
        logger.error("Error processing callback: %s", str(error), exc_info=True)
        # Return a 200 response to Twilio even on error to prevent retries
        return Response(content="Error processed", status_code=200)

@router.get("/aicalling/reports/{agent_id}/{date}")
async def download_ai_calling_records(date: str, agent_id: str) -> Response:
    """
    Download AI calling records as CSV for a specific country code and date
    Args:
        date: Date in YYYY-MM-DD format
    Returns:
        Response containing CSV file
    """
    try:
        stream_service = ReminderService()
        csv_path = stream_service.generate_calls_csv(date, agent_id)
        
        # Read the CSV file
        with open(csv_path, "rb") as filename:
            csv_content = filename.read()
            
        # Return CSV file as response
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{csv_path}"'},
        )
    except Exception as error:
        logger.error("Error generating CSV file: %s", error)
        raise HTTPException(status_code=500, detail="Failed to generate CSV file") from error

@router.post("/update-recent-records")
async def update_recent_records(background_tasks: BackgroundTasks) -> Dict:
    """
    Args:
        background_tasks: FastAPI background tasks handler
    
    Returns:
        dict: Message indicating the update process has been initiated
    """
    logger.info("Updating recent records")
    try:
        stream_service = ReminderService()
        background_tasks.add_task(stream_service.update_recent_call_records)
        return {"success": True, "message": "Update process initiated"}
    except Exception as error:
        logger.error("Error initiating recent records update: %s", error)
        raise HTTPException(status_code=500, detail="Failed to initiate update process") from error

@router.post("/outbound-call-twiml")
async def initiate_call(
    request: Request,
    first_message: str = Query(...),
    time: str = Query(...),
    calling_to: str = Query(...),
    prompt: str = Query(...),
    phone_number: str = Query(...),
    agent_id: str = Query(...)
) -> Response:
    """
    Generate TwiML response for initiating an outbound call.

    Args:
        request: FastAPI request object
        prompt: The conversation prompt
        first_message: Initial message to be spoken
        agent_id: ID of the AI agent
        to_number: Recipient's phone number
        country_code: Country code for the call

    Returns:
        Response containing TwiML instructions
    """
    logger.info(f"Received parameters: first_message=%s, time=%s, calling_to=%s, prompt=%s, phone_number=%s, agent_id=%s",
                first_message, time, calling_to, prompt, phone_number, agent_id)
    
    logger.info("base_url: %s", request.base_url)

    if not all([first_message, time, prompt, calling_to, phone_number, agent_id]):
        raise HTTPException(status_code=422, detail="Missing required parameters")

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="wss://{request.base_url.hostname}/outbound-media-stream">
                <Parameter name="first_message" value="{first_message}" />
                <Parameter name="time" value="{time}" />
                <Parameter name="calling_to" value="{calling_to}" />
                <Parameter name="prompt" value="{prompt}" />
                <Parameter name="phone_number" value="{phone_number}" />
                <Parameter name="agent_id" value="{agent_id}" />
            </Stream>
        </Connect>
    </Response>"""

    logger.info("Generated TwiML response: %s", twiml_response)
    return Response(content=twiml_response, media_type="application/xml")

@router.websocket("/outbound-media-stream")
async def outbound_media_stream(websocket: WebSocket):
    """WebSocket route for handling media streams"""
    await websocket.accept()
    logger.info("[Server] Twilio connected to outbound media stream")
    
    stream_service = None

    try:
        async for message in websocket.iter_text():
            try:
                msg = json.loads(message)
                logger.info("[Twilio] Received message: %s", msg)

                if msg["event"] == "start":
                    stream_sid = msg["start"]["streamSid"]
                    custom_parameters = msg["start"]["customParameters"]
                    logger.info("[Twilio] Custom parameters: %s", custom_parameters)
                    
                    stream_service = ReminderService(
                        to_number=custom_parameters.get("calling_to"),
                        websocket=websocket,
                        stream_sid=stream_sid
                    )
                    await stream_service.setup_connection(custom_parameters)
                    asyncio.create_task(stream_service.keep_alive())

                elif msg["event"] == "media" and stream_service:
                    await stream_service.handle_media(msg["media"]["payload"])

                elif msg["event"] == "stop":
                    logger.info("[Twilio] Received stop event")
                    break

            except json.JSONDecodeError as error:
                logger.error("[Twilio] Error decoding message: %s", error)
            except KeyError as error:
                logger.error("[Twilio] Missing required field in message: %s", error)
            except (RuntimeError, ConnectionError) as error:
                logger.error("[Twilio] Error processing message: %s", error)

    except (websockets.exceptions.WebSocketException, json.JSONDecodeError, asyncio.CancelledError) as error:
        logger.error("[Server] WebSocket error: %s", error)
        raise

    finally:
        logger.info("[Server] Cleaning up connections")
        if stream_service:
            await stream_service.cleanup()
            logger.info("[Server] Connections cleaned up")

@router.get("/aicalling/agents")
async def get_elevenlabs_agents() -> dict:
    """
    Get list of available ElevenLabs agents.

    Returns:
        dict: List of agents and their details
    """
    try:
        stream_service = ReminderService()
        agents = await stream_service.get_elevenlabs_agents()
        return {"success": True, "agents": agents}
    except Exception as error:
        logger.error("Error fetching ElevenLabs agents: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch agents") from error