from datetime import datetime, timedelta, timezone
from config import settings
from logger import logger

class CallRepository:
    gsi1_name = "ConversationIdIndex"
    
    def __init__(self, dynamodb_client):
        self.dynamodb = dynamodb_client
        self.table_name = settings.dynamodb_table
        self.table = dynamodb_client.Table(settings.dynamodb_table)

    def create_ai_call_item(self, time, calling_to, prompt, agent_id, status):
        logger.info(
            "Creating AI call item - Time: %s, Calling to: %s, Prompt: %s, Agent ID: %s, Status: %s",
            time,
            calling_to,
            prompt,
            agent_id,
            status
        )
        
        created_at = datetime.now(timezone.utc).isoformat()
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        partition_key = f"AICalling#{agent_id}#{current_date}"
        
        item = {
            "PK": partition_key,
            "SK": calling_to,
            "stage": status,
            "conversation_id": "nil",
            "created_at_utc": created_at,
            "prompt": prompt,
            "time": time,
            "GSI1PK": "nil"
        }
        
        self.table.put_item(Item=item)
        logger.info("Created bank statement item in db: %s", item)

    def update_ai_call_item(self, destination_number, agent_id, updates: dict):
        logger.info(
            "Updating AI call item for number: %s with agent_id: %s and updates: %s",
            destination_number,
            agent_id,
            updates,
        )

        # Create the partition key
        partition_key = f"AICalling#{agent_id}#{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        
        # Correct key structure
        key = {
            "PK": partition_key,
            "SK": destination_number
        }

        # Build update expression
        update_parts = []
        expression_values = {}
        expression_names = {}

        # If conversation_id is being updated, also update the GSI1PK
        if "conversation_id" in updates:
            updates["GSI1PK"] = updates["conversation_id"]

        for i, (field, value) in enumerate(updates.items()):
            placeholder = f":val{i}"
            attr_name = f"#attr{i}"
            update_parts.append(f"{attr_name} = {placeholder}")
            expression_values[placeholder] = value
            expression_names[attr_name] = field

        update_expression = "SET " + ", ".join(update_parts)

        # Update the item in the database
        response = self.table.update_item(
            Key=key,
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ExpressionAttributeNames=expression_names,
        )

        logger.info("DynamoDB update_item response: %s", response)
        logger.info("Updated AI call item in db with values: %s", updates)

    def get_call_by_conversation_id(self, conversation_id):
        """Retrieve a call record using the conversation_id GSI"""
        try:
            response = self.table.query(
                IndexName=self.gsi1_name,
                KeyConditionExpression="#gsi1pk = :conversation_id",
                ExpressionAttributeNames={"#gsi1pk": "GSI1PK"},
                ExpressionAttributeValues={":conversation_id": conversation_id},  # Remove the {"S": ...} wrapper
            )
            logger.info("DynamoDB query response for conversation_id %s: %s", conversation_id, response)
            return response.get("Items", [])
        except Exception as error:
            logger.error("Error querying by conversation_id: %s", error)
            raise

    def update_call_criteria_record(self, gsi1pk: str, updates: dict):
        """
        Update a record using GSI1PK (conversation_id)
        Args:
            gsi1pk: The GSI1PK value to query by
            updates: Dictionary containing fields and values to update
        """
        try:
            # First get the record using GSI1PK to get PK and SK
            items = self.get_call_by_conversation_id(gsi1pk)

            if not items:
                logger.error("No record found for GSI1PK: %s", gsi1pk)
                return

            # Group key-related data
            key_data = {"pk": items[0]["PK"], "sk": items[0]["SK"]}

            if not key_data["pk"] or not key_data["sk"]:
                logger.error("Missing PK or SK in record for GSI1PK: %s", gsi1pk)
                return

            # Group expression-related variables
            expr = {"parts": [], "values": {}, "names": {}}

            for i, (field, value) in enumerate(updates.items()):
                placeholder = f":val{i}"
                attr_name = f"#attr{i}"
                expr["parts"].append(f"{attr_name} = {placeholder}")
                expr["values"][placeholder] = {"S": str(value)}
                expr["names"][attr_name] = field

            # Update the item using PK and SK
            response = self.table.update_item(
                Key={"PK": key_data["pk"], "SK": key_data["sk"]},  # Remove the {"S": ...} wrapper
                UpdateExpression="SET " + ", ".join(expr["parts"]),
                ExpressionAttributeValues=expr["values"],
                ExpressionAttributeNames=expr["names"],
            )

            logger.info("Updated record for GSI1PK %s with values: %s", gsi1pk, updates)
            logger.info("DynamoDB update response: %s", response)
        except Exception as error:
            logger.error("Error updating call criteria record: %s", error)
            raise

    def get_calls_by_date(self, date: str, agent_id: str) -> list:
        """
        Get all AI calling records for a specific date and agent
        Args:
            date: Date in YYYY-MM-DD format
            agent_id: Agent ID string
        Returns:
            List of call records
        """
        try:
            partition_key = f"AICalling#{agent_id}#{date}"
            response = self.table.query(
                KeyConditionExpression="#pk = :pk",
                ExpressionAttributeNames={"#pk": "PK"},
                ExpressionAttributeValues={":pk": partition_key},  # Removed {"S": ...} wrapper
            )

            # Convert DynamoDB items to regular Python dictionaries
            items = []
            for item in response.get("Items", []):
                converted_item = {}
                for key, value in item.items():
                    # If the value is already a primitive type, use it directly
                    if not isinstance(value, dict):
                        converted_item[key] = value
                    else:
                        # Extract the actual value from DynamoDB type dict
                        converted_item[key] = next(iter(value.values()))
                items.append(converted_item)

            logger.info("Retrieved %d records on date %s", len(items), date)
            return items

        except Exception as error:
            logger.error("Error querying calls by date: %s", error)
            raise

    def get_recent_calls(self, minutes: int = 20) -> list:
        # Calculate time window
        now = datetime.utcnow()
        twenty_mins_ago = now - timedelta(minutes=minutes)

        # Format timestamps as strings without the "+00:00" suffix
        start_time = twenty_mins_ago.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        end_time = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        try:
            response = self.table.scan(
                FilterExpression="#created_at BETWEEN :start_time AND :end_time",
                ExpressionAttributeNames={
                    "#created_at": "created_at_utc"
                },
                ExpressionAttributeValues={
                    ":start_time": start_time,  # Remove the {"S": ...} wrapper
                    ":end_time": end_time      # Remove the {"S": ...} wrapper
                }
            )

            # Convert DynamoDB items to regular Python dictionaries
            items = []
            for item in response.get("Items", []):
                converted_item = {}
                for key, value in item.items():
                    # Check if the value is a DynamoDB type dict
                    if isinstance(value, dict):
                        converted_item[key] = next(iter(value.values()))
                    else:
                        # If it's already a primitive value, use it directly
                        converted_item[key] = value
                items.append(converted_item)

            return items

        except Exception as error:
            print(f"Error querying recent calls: {str(error)}")
            raise