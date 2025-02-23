import asyncio
import websockets
import json
import base64
import httpx
import pandas as pd
from datetime import datetime
from app.modules.models import CallRequest
from app.modules.ai_client import twilio_client
from urllib.parse import urlencode
from logger import logger
from typing import List
from app.modules.repositories import CallRepository
from config import settings
from aws_client import dynamodb
from app.modules.constants import CALL_STATUS

class ReminderService:
    def __init__(self, to_number=None, websocket=None, stream_sid=None):
        self.websocket = websocket
        self.stream_sid = stream_sid
        self.to_number = to_number
        self.eleven_labs_ws = None
        self.eleven_labs_task = None
        self.call_repository = CallRepository(dynamodb)
        self.conversation_id = None

    @staticmethod
    async def create_call(call_request: CallRequest, host: str):
        try:
            logger.info(f"Creating call for number: {call_request.phone_number}")
            
            # Create the callback URL for call status updates
            status_callback = f"https://{host}/elevenlabs/callback/outbound-call-status"

            call_request.calling_to = "+" + str(call_request.calling_to)
            if call_request.event_type == "Medicine":
                call_request.prompt = call_request.prompt + ". Calling For: " + call_request.event_type + ", Medicine name: " + call_request.event_name
            elif call_request.event_type == "Vital":
                call_request.prompt = call_request.prompt + ". Calling For: " + call_request.event_type + ", Vital name: " + call_request.event_name
            logger.info("Call request details: %s", call_request.model_dump())
            # Convert call_request to dictionary and encode parameters
            call_params = call_request.model_dump()
            encoded_params = urlencode(call_params)

            # Create initial record in DynamoDB
            call_repository = CallRepository(dynamodb)
            call_repository.create_ai_call_item(
                time=call_request.time,
                calling_to=call_request.calling_to,
                prompt=call_request.prompt,
                agent_id=call_request.agent_id,
                status=CALL_STATUS["call_initiated"]
            )
            
            # Create outbound call using Twilio
            call = twilio_client.calls.create(
                to=call_request.calling_to,
                from_=call_request.phone_number,  # Assuming you have this in your CallRequest model
                url=f"https://{host}/outbound-call-twiml?{encoded_params}",
                status_callback=status_callback,
                status_callback_event=['initiated', 'ringing', 'answered', 'completed']
            )
            logger.info(f"Call created successfully with SID: {call.sid}")
            
        except Exception as e:
            logger.error(f"Error creating call: {str(e)}", exc_info=True)
            raise

    def generate_calls_csv(self, date: str, agent_id: str) -> str:
        """
        Generate a CSV file containing AI calling records for a specific date and agent
        
        Args:
            date: Date in YYYY-MM-DD format
            agent_id: Agent ID string
            
        Returns:
            str: Path to the generated CSV file
        """
        try:
            # Validate date format
            datetime.strptime(date, "%Y-%m-%d")
            
            # Get records from repository
            call_repository = CallRepository(dynamodb)
            records = call_repository.get_calls_by_date(date, agent_id)
            logger.info("Retrieved records: %s", records)

            if not records:
                logger.warning(f"No records found for date {date} and agent {agent_id}")
                records = []

            # Create DataFrame
            dataframe = pd.DataFrame(records)

            # Define column mappings
            column_mappings = {
                "SK": "Contact Number",
                "stage": "Call Status",
                "time": "Time",
                "medicine_taken": "Medicine Taken",
                "blood_glucose_level": "Blood Glucose Level",
                "systolic_blood_pressure": "Systolic BP",
                "diastolic_blood_pressure": "Diastolic BP"
            }

            # Handle empty DataFrame
            if dataframe.empty:
                dataframe = pd.DataFrame(columns=list(column_mappings.values()))
            else:
                # First ensure all source columns exist
                for source_col in column_mappings.keys():
                    if source_col not in dataframe.columns:
                        dataframe[source_col] = None
                
                # Then rename columns according to mapping
                dataframe = dataframe.rename(columns=column_mappings)

            # Add date column with proper name
            dataframe["Date"] = date

            # Select and reorder columns
            columns = [
                "Date",
                "Time",
                "Contact Number", 
                "Call Status",
                "Medicine Taken",
                "Blood Glucose Level",
                "Systolic BP",
                "Diastolic BP"
            ]
            
            # Ensure all final columns exist
            for col in columns:
                if col not in dataframe.columns:
                    dataframe[col] = None
                    
            dataframe = dataframe[columns]

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ai_calls_{date}_{timestamp}.csv"

            # Write to CSV
            dataframe.to_csv(filename, index=False)

            logger.info(f"Generated CSV file: {filename}")
            return filename

        except ValueError as ve:
            logger.error(f"Invalid date format: {str(ve)}")
            raise
        except Exception as error:
            logger.error(f"Error generating CSV file: {str(error)}")
            raise

    def get_ai_calling_records_service(self, date: str, agent_id: str) -> List:
        """
        Args:
            date: Date in YYYY-MM-DD format 
        Returns:
            List of call records
        """
        try:
            call_repository = CallRepository(dynamodb)
            records = call_repository.get_calls_by_date(date, agent_id)
            logger.info("Retrieved %d records on date %s", len(records), date)
            return records
        except Exception as error:
            logger.error("Error in get_ai_calling_records_service: %s", error)
            raise

    async def handle_eleven_labs_messages(self):
        """Handles the WebSocket message stream from ElevenLabs"""
        try:
            logger.info("[ElevenLabs] Starting message handling loop")
            async for message in self.eleven_labs_ws:
                await self._process_message(message)
        except (websockets.exceptions.WebSocketException, json.JSONDecodeError) as error:
            logger.error("[ElevenLabs] Message loop error: %s", error)
            raise
        except (ValueError, RuntimeError, KeyError) as error:
            logger.error("[ElevenLabs] Message loop error: %s", error)
            raise

    async def _process_message(self, message):
        """Process individual messages from ElevenLabs"""
        logger.info("[ElevenLabs] Raw message received: %s", message)
        try:
            msg = json.loads(message)
            logger.info("[ElevenLabs] Received message type: %s", msg.get("type"))

            handlers = {
                "audio": self._handle_audio_message,
                "interruption": self._handle_interruption,
                "ping": self._handle_ping,
                "error": self._handle_error,
                "conversation_started": self._handle_conversation_started,
            }

            handler = handlers.get(msg["type"])
            if handler:
                await handler(msg)

        except json.JSONDecodeError as error:
            logger.error("[ElevenLabs] Failed to parse message as JSON: %s", error)
        except (ValueError, RuntimeError, KeyError) as error:
            logger.error("[ElevenLabs] Error processing message: %s", error)

    
    async def _handle_audio_message(self, msg):
        """Handle audio messages from ElevenLabs"""
        try:
            audio_payload = msg["audio_event"].get("audio_base_64")
            if not audio_payload:
                logger.error("[ElevenLabs] No audio payload found in message")
                return

            logger.info("[ElevenLabs] Successfully extracted audio payload")
            audio_data = {"event": "media", "streamSid": self.stream_sid, "media": {"payload": audio_payload}}
            await self.websocket.send_text(json.dumps(audio_data))
            logger.info("[ElevenLabs] Successfully sent audio to Twilio")

        except (KeyError, json.JSONDecodeError) as error:
            logger.error("[ElevenLabs] Error processing audio message data: %s", error)
        except websockets.exceptions.WebSocketException as error:
            logger.error("[ElevenLabs] WebSocket error sending audio: %s", error)
        except RuntimeError as error:
            logger.error("[ElevenLabs] Runtime error processing audio: %s", error)

    async def _handle_interruption(self, msg):
        """Handle interruption messages"""
        await self.websocket.send_text(json.dumps({"event": "clear", "streamId": self.stream_sid}))

    async def _handle_ping(self, msg):
        """Handle ping messages"""
        if msg.get("ping_event", {}).get("event_id"):
            await self.eleven_labs_ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))

    async def _handle_error(self, msg):
        """Handle error messages"""
        logger.error("[ElevenLabs] Received error message: %s", msg)

    # pylint: disable=unused-argument
    async def _handle_conversation_started(self, msg):
        """Handle conversation started messages"""
        logger.info("[ElevenLabs] Conversation started successfully")

    async def handle_call_status(self, to_number, status, agent_id=None):
        """Handle call status updates from Twilio"""
        logger.info("[Twilio] Call status update: %s", status)
        try:
            if status in ["busy", "no-answer", "failed", "canceled"]:
                if to_number:
                    self.call_repository.update_ai_call_item(to_number, agent_id, {"stage": status})
        except (ValueError, RuntimeError, ConnectionError) as error:
            logger.error("[Twilio] Error handling call status: %s", error)

    async def keep_alive(self):
        """Sends periodic keepalive pings to ElevenLabs"""
        while True:
            try:
                if self.eleven_labs_ws and not self.eleven_labs_ws.closed:
                    await self.eleven_labs_ws.send(json.dumps({"type": "ping"}))
                    logger.info("[ElevenLabs] Sent keepalive ping")
                    await asyncio.sleep(30)
            except (websockets.exceptions.WebSocketException, ConnectionError, RuntimeError) as error:
                logger.error("[ElevenLabs] Keepalive error: %s", error)
                break

    async def setup_connection(self, custom_parameters):
        """Sets up the ElevenLabs WebSocket connection"""
        try:
            signed_url = await self.get_signed_url(custom_parameters)
            logger.info("[ElevenLabs] Got signed URL: %s", signed_url)

            try:
                self.eleven_labs_ws = await websockets.connect(signed_url, ping_interval=20, ping_timeout=60)
                logger.info("[ElevenLabs] Connected to websocket")

                # Listen for the initial metadata message
                initial_message = await self.eleven_labs_ws.recv()
                metadata = json.loads(initial_message)

                if metadata["type"] == "conversation_initiation_metadata":
                    logger.info("[ElevenLabs] Received metadata: %s", metadata)
                    self.conversation_id = metadata["conversation_initiation_metadata_event"]["conversation_id"]
                    self.call_repository.update_ai_call_item(
                        custom_parameters.get("calling_to"),
                        custom_parameters.get("agent_id"),
                        {"conversation_id": self.conversation_id}
                    )

            except websockets.exceptions.WebSocketException as error:
                logger.error("[ElevenLabs] WebSocket connection failed: %s", error)
                raise

            await self._send_initial_config(custom_parameters)
            self.eleven_labs_task = asyncio.create_task(self.handle_eleven_labs_messages())
            logger.info("[ElevenLabs] Message handling task created")

        except Exception as error:
            logger.error("[ElevenLabs] Setup error: %s", error)
            self.call_repository.update_ai_call_item(
                custom_parameters.get("calling_to"),
                custom_parameters.get("agent_id"),
                {"stage": CALL_STATUS["call_failed"]},
            )
            raise

    async def _send_initial_config(self, custom_parameters):
        """Sends initial configuration to ElevenLabs"""
        initial_config = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": custom_parameters.get("prompt", "")},
                    "first_message": custom_parameters.get("first_message", ""),
                }
            },
        }

        logger.info("[ElevenLabs] Sending initial config: %s", initial_config)
        await self.eleven_labs_ws.send(json.dumps(initial_config))
        logger.info("[ElevenLabs] Initial config sent successfully")

    async def handle_media(self, payload):
        """Handles incoming media from Twilio"""
        logger.info("[Twilio] Received audio from user")
        audio_message = {"user_audio_chunk": base64.b64encode(base64.b64decode(payload)).decode()}
        await self.eleven_labs_ws.send(json.dumps(audio_message))
        logger.info("[Twilio] Sent audio to ElevenLabs")

    async def cleanup(self):
        """Cleans up WebSocket connections and tasks"""
        if self.eleven_labs_ws:
            if self.conversation_id:
                logger.info("[ElevenLabs] Cleaning up conversation ID: %s", self.conversation_id)
                await self.eleven_labs_ws.close()
            
            if self.eleven_labs_task:
                self.eleven_labs_task.cancel()
                try:
                    await self.eleven_labs_task
                except asyncio.CancelledError:
                    pass

    def get_elevenlabs_conversation_analysis(self, conversation_id):
        """Fetches conversation details from ElevenLabs API"""
        logger.info("[ElevenLabs] Fetching conversation details for conversation ID: %s", conversation_id)
        try:
            response = httpx.Client().get(
                f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
            response.raise_for_status()
            json_response = response.json()
            logger.info("[ElevenLabs] Conversation details: %s", json_response)
            
            # Extract evaluation criteria results
            analysis = json_response.get("analysis", {})
            if not analysis:
                return {}
                
            criteria_results = analysis.get("evaluation_criteria_results", {})
            # Create dict with criteria names and their results
            results_dict = {
                criteria: data.get("result") 
                for criteria, data in criteria_results.items()
            }
            
            # Extract data collection results
            data_collection = analysis.get("data_collection_results", {})
            vital_keys = ["Diastolic blood pressure", "blood glucose level", "Systolic blood pressure"]
            
            # Add vital readings if they have values
            for key in vital_keys:
                if key in data_collection and data_collection[key].get("value") is not None:
                    # Convert key to snake_case for consistency
                    snake_case_key = key.lower().replace(" ", "_")
                    results_dict[snake_case_key] = data_collection[key]["value"]
            
            # Add call_successful status if present
            if call_status := analysis.get("call_successful"):
                results_dict["call_successful"] = call_status
                
            logger.info("[ElevenLabs] Evaluation criteria results: %s", results_dict)
            return results_dict
                
        except httpx.HTTPError as error:
            logger.error("[ElevenLabs] HTTP error fetching conversation details: %s", error)
            return {}
        except json.JSONDecodeError as error:
            logger.error("[ElevenLabs] JSON decode error fetching conversation details: %s", error)
            return {}
        except (KeyError, ValueError) as error:
            logger.error("[ElevenLabs] Data processing error fetching conversation details: %s", error)
            return {}

    async def get_signed_url(self, custom_parameters):
        """Helper function to get signed URL for authenticated conversations"""
        try:
            async with httpx.AsyncClient() as client:
                logger.info("[ElevenLabs] Agent ID from custom parameters: %s", custom_parameters.get("agent_id"))
                response = await client.get(
                    f"https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id={custom_parameters.get('agent_id')}",
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                )
                response.raise_for_status()
                data = response.json()
                return data["signed_url"]
        except Exception as error:
            logger.error("Error getting signed URL: %s", error)
            raise

    async def update_recent_call_records(self) -> list:
        """
        Update AI calling records from the last 20 minutes with ElevenLabs conversation analysis
        
        Returns:
            list: List of updated call records
        """
        try:
            call_repository = CallRepository(dynamodb)
            records = call_repository.get_recent_calls(minutes=200)
            logger.info("Retrieved records: %s", records)

            for record in records:
                if conversation_id := record.get("conversation_id"):
                    try:
                        call_feedback = self.get_elevenlabs_conversation_analysis(conversation_id)
                        call_repository.update_call_criteria_record(conversation_id, call_feedback)
                    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as error:
                        logger.error(
                            "Error fetching ElevenLabs details for conversation %s: %s", conversation_id, str(error)
                        )

            logger.info("Updated %d records from the last 20 minutes", len(records))
            return records
            
        except Exception as error:
            logger.error("Error in update_recent_call_records: %s", error)
            raise

    async def get_elevenlabs_agents(self) -> list:
        """
        Fetches list of available agents from ElevenLabs API
        
        Returns:
            list: List of agent objects containing their details
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.elevenlabs.io/v1/convai/agents",
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                )
                response.raise_for_status()
                return response.json()
        except Exception as error:
            logger.error("[ElevenLabs] Error fetching agents list: %s", error)
            raise