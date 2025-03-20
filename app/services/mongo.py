import re
from datetime import datetime
from typing import Tuple, Dict

from bson import ObjectId, SON
from pymongo import MongoClient, ReturnDocument, DESCENDING, ASCENDING

from app.services.interfaces.idatabase_service import DatabaseService


class Mongo(DatabaseService):
    @staticmethod
    def sanitize_label(value: str) -> str:
        """Sanitizes user input by escaping special characters used in NoSQL injections."""
        if not isinstance(value, str):
            raise ValueError("Invalid input: must be a string")
        return re.sub(r"([\$\.\{\}\[\]\(\)])", r"\\\1", value)  # Escaping special characters

    def sanitize_query(self, query: dict) -> dict:
        """Sanitizes MongoDB queries while preserving aggregation operators."""
        if not isinstance(query, dict):
            raise ValueError("Invalid query: must be a dictionary")

        sanitized_query = {}
        for key, value in query.items():
            if not isinstance(key, str):
                continue  # Ignore non-string keys

            # ✅ Preserve MongoDB operators ($match, $sort, etc.)
            if key.startswith("$"):
                sanitized_query[key] = value
            else:
                sanitized_key = re.sub(r"[\$\.\{\}\[\]\(\)]", "", key)  # Remove special characters
                sanitized_value = self.sanitize_value(value)
                sanitized_query[sanitized_key] = sanitized_value

        return sanitized_query

    def sanitize_value(self, value):
        """Sanitizes individual values within a query."""
        if isinstance(value, str):
            return re.sub(r"[\$\.\{\}\[\]\(\)]", "", value)  # Remove special characters
        elif isinstance(value, dict):
            return self.sanitize_query(value)  # Recursively sanitize nested queries
        elif isinstance(value, list):
            return [self.sanitize_value(item) for item in value]  # Sanitize lists
        return value  # Return unchanged for numbers, booleans, etc.

    def sanitize_pipeline(self, pipeline: list) -> list:
        """Sanitizes aggregation pipelines while preserving MongoDB operators."""
        if not isinstance(pipeline, list):
            raise ValueError("Invalid pipeline: must be a list")

        sanitized_pipeline = []
        for stage in pipeline:
            if not isinstance(stage, dict):
                continue  # Ignore invalid pipeline stages

            sanitized_stage = {}
            for key, value in stage.items():
                if key.startswith("$"):  # ✅ Preserve MongoDB operators
                    sanitized_stage[key] = self.sanitize_value(value)
                else:
                    sanitized_stage[self.sanitize_query({key: value})] = value

            sanitized_pipeline.append(sanitized_stage)

        return sanitized_pipeline

    def react(self, _id: str, _collection: str, _label: str, user_id: str) -> tuple[dict[str, str], int]:
        collection = self.db[_collection]
        reactions_collection = self.db['labels']
        document = collection.find_one({"_id": ObjectId(_id)})

        label_exists = reactions_collection.find_one(SON({"name": self.sanitize_label(_label)}))
        if not label_exists:
            return {"error": f"Label '{_label}' does not exist in labels collection"}, 400

        if not document:
            return {"error": "Document not found"}, 400

        # Find the label inside the labels array
        labels = document.get('labels', [])
        for label in labels:
            if str(label["label_id"]) == str(label_exists.get('_id')):
                if user_id not in label["users"]:
                    # Append user to the label's user list
                    collection.update_one(
                        {"_id": ObjectId(_id), "labels.label_id": ObjectId(label_exists.get('_id'))},
                        {"$addToSet": SON({"labels.$.users": self.sanitize_label(user_id)})}
                    )
                    return {"message": f"User {user_id} reacted with {_label}"}, 200
                else:
                    collection.update_one(
                        {"_id": ObjectId(_id), "labels.label_id": ObjectId(label_exists.get('_id'))},
                        {"$pull": SON({"labels.$.users": self.sanitize_label(user_id)})}
                    )
                    return {"message": f"User {user_id} dereacted with {_label}"}, 200

        # If label does not exist in document, add it
        collection.update_one(
            {"_id": ObjectId(_id)},
            {"$push": {"labels": SON({"label_id": ObjectId(label_exists.get('_id')), "users": [self.sanitize_label(user_id)]})}}
        )

        return {"message": f"User {user_id} reacted with created {_label}"}, 200

    def create_fact(self, title: str, context: str, page: int, quote: str, source_id: str, user_id: str) -> dict:
        if source_id is None or source_id == "":
            return {"error": "Source is missing"}
        if user_id is None or user_id == "":
            return {"error": "User is missing"}

        collection = self.db['facts']
        query = SON({'title': title})
        document = SON({
            'title': self.sanitize_label(title),
            'context': self.sanitize_label(context),
            'page': int(page),
            'quote': self.sanitize_label(quote),
            'source_id': ObjectId(source_id),
            'user_id': self.sanitize_label(user_id),
            'labels': [],
            'created_at': datetime.utcnow(),
        })
        return dict(collection.find_one_and_update(query, {"$set": document}, upsert=True, return_document=ReturnDocument.AFTER))

    def create_source(self, title: str, author: str, year: int, _type_id: str, link: str) -> dict:
        type_exists = self.db['types'].find_one({"_id": ObjectId(_type_id)})
        if not type_exists:
            return {"error": f"Label '{_type_id}' does not exist in labels collection"}

        collection = self.db['sources']
        query = {'title': self.sanitize_label(title)}
        document = SON({
            'title': self.sanitize_label(title),
            'author': self.sanitize_label(author),
            'year': int(year),
            'type': ObjectId(_type_id),
            'link': self.sanitize_label(link),
            'labels': [],
            'created_at': datetime.utcnow(),
        })
        return dict(collection.find_one_and_update(query, {"$set": document}, upsert=True,
                                                   return_document=ReturnDocument.AFTER))

    def __init__(self, database_name, host):
        self.client = MongoClient(host)
        self.db = self.client[database_name]

    def get_documents(self, collection: str, query: dict) -> list[dict]:
        collection = self.db[collection]
        return list(collection.find(self.sanitize_query(query)))

    def get_documents_pipeline(self, collection: str, pipeline: list) -> list[dict]:
        for stage in pipeline:
            if "$sort" in stage:
                sort_field = list(stage["$sort"].keys())[0]
                sort_order_str = list(stage["$sort"].values())[0]
                stage["$sort"][sort_field] = DESCENDING if sort_order_str.lower() == "desc" else ASCENDING
                break

        collection = self.db[collection]
        return list(collection.aggregate(self.sanitize_pipeline(pipeline)))

    def get_document(self, collection: str, query: dict) -> dict:
        for key in query:
            if "id" in key.lower() and isinstance(query[key], str):  # Check if key contains "id" and value is a string
                try:
                    query[key] = ObjectId(query[key])  # Convert to ObjectId
                except Exception:
                    pass
        collection = self.db[collection]
        return collection.find_one(self.sanitize_query(query))

    def delete_document(self, collection: str, query: dict):
        for key in query:
            if "id" in key.lower() and isinstance(query[key], str):  # Check if key contains "id" and value is a string
                try:
                    query[key] = ObjectId(query[key])  # Convert to ObjectId
                except Exception:
                    pass
        self.db[collection].delete_one(self.sanitize_query(query))

    def count_documents(self, collection: str, query: dict) -> int:
        collection = self.db[collection]
        return collection.count_documents(self.sanitize_query(query))

    def get_user_summary(self, user_id: str):
        """Retrieves user summary safely."""

        if not isinstance(user_id, str):
            user_id = '^$'

        pipeline = [
            {"$match": {"user_id": self.sanitize_label(user_id)}},  # Filter documents by user_id
            {"$unwind": "$labels"},  # Flatten the labels array
            {
                "$group": {
                    "_id": "$labels.label_id",  # Group by label_id
                    "reaction_count": {"$sum": {"$size": "$labels.users"}},  # Count users (reactions) per label
                }
            },
            {"$sort": {"reaction_count": -1}}  # Sort by most reactions
        ]
        label_summary = list(self.db['facts'].aggregate(pipeline))
        labels = self.get_documents('labels', {})
        for label in labels:
            label_source = None
            for l in label_summary:
                if l.get('_id') == label.get('_id'):
                    label_source = l
            label.update({'reaction_count': label_source.get('reaction_count') if label_source else 0})

        pipeline = [
            {"$match": {"user_id": {"$exists": True, "$eq": self.sanitize_label(user_id)}}},  # Filter where user_id matches
            {
                "$project": {
                    "_id": 1,  # Include document ID
                    "title": 1,  # Include title
                    "source_id": 1  # Include labels array
                }
            },
            {
                "$lookup": {
                    "from": "sources",        # Collection to join
                    "localField": "source_id", # Field in current collection
                    "foreignField": "_id",     # Field in 'sources' collection
                    "as": "source_info",        # Output field
                    "pipeline": [
                        {
                            "$lookup": {  # Second lookup for type details
                                "from": "types",
                                "localField": "type",
                                "foreignField": "_id",
                                "as": "type_info"
                            }
                        },
                        {
                            "$project": {  # Projection inside lookup
                                "_id": 1,  # Include _id
                                "title": 1,  # Include source name
                                "author": 1,  # Include publication year
                                "year": 1,  # Include publication year
                                "type_info": 1  # Include publication year
                            }
                        }
                    ]
                }
            },
        ]
        facts = list(self.db['facts'].aggregate(pipeline))

        return facts, labels

