import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

class Database:
    client: AsyncIOMotorClient = None
    db = None
    is_connected = False

db_instance = Database()


async def ensure_indexes(db):
    """Create data-integrity indexes without making startup unavailable."""
    indexes = [
        ("users", "email", True),
        ("users", "id", True),
        ("tasks", "task_id", True),
        ("tasks", "parent_task_id", False),
        ("incidents", "id", True),
        ("surveys", "id", True),
        ("audit_logs", "id", True),
        ("audit_logs", "timestamp", False),
        ("trash", "item_id", False),
    ]
    for collection, field, unique in indexes:
        try:
            index_options = {
                "unique": unique,
                "name": f"{field}_{'unique' if unique else 'idx'}",
            }
            if collection == "tasks" and field == "task_id":
                # Legacy documents without task_id must not block uniqueness
                # for real task identifiers.
                index_options["partialFilterExpression"] = {
                    "task_id": {"$type": "string"}
                }
            await db[collection].create_index(
                [(field, 1)],
                **index_options,
            )
        except Exception as exc:
            # Existing duplicates must be reviewed before enforcing uniqueness.
            print(f"   [DB INDEX WARN] {collection}.{field}: {exc}")
    try:
        await db.pci_history.create_index(
            [("segment_id", 1), ("survey_id", 1)],
            unique=True,
            name="segment_survey_unique",
        )
    except Exception as exc:
        print(f"   [DB INDEX WARN] pci_history.segment_id+survey_id: {exc}")
    try:
        await db.batch_results.create_index(
            [("task_id", 1), ("has_detection", 1), ("result_index", 1)],
            name="task_detection_result_idx",
        )
        await db.tasks.create_index(
            [("parent_task_id", 1), ("processingStatus", 1)],
            name="parent_processing_status_idx",
        )
    except Exception as exc:
        print(f"   [DB INDEX WARN] batch processing indexes: {exc}")


async def connect_to_mongo():
    """Continuous retry loop to connect to MongoDB."""
    attempt = 1
    while not db_instance.is_connected:
        try:
            print(f"   [DB] Connection attempt {attempt} to {settings.MONGODB_URL}...")
            db_instance.client = AsyncIOMotorClient(
                settings.MONGODB_URL,
                # v8.0: Optimized for high-latency Tailscale network
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=5000,
                maxPoolSize=100,
                minPoolSize=10,
                retryWrites=True
            )
            # Try to list databases to verify connection
            await db_instance.client.list_database_names()
            
            db_instance.db = db_instance.client[settings.DATABASE_NAME]
            db_instance.is_connected = True
            await ensure_indexes(db_instance.db)
            print(f"   [DB] SUCCESS: Connected to {settings.DATABASE_NAME}")
        except Exception as e:
            print(f"   [DB] Attempt {attempt} failed: {e}")
            db_instance.is_connected = False
            db_instance.db = None
            await asyncio.sleep(5) # Wait 5s before retrying
            attempt += 1

async def close_mongo_connection():
    if db_instance.client:
        db_instance.client.close()
        db_instance.is_connected = False
        print("   [DB] Connection closed")

def get_db():
    if not db_instance.is_connected:
        return None
    return db_instance.db
