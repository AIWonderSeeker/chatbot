import os

# Keep tests hermetic: never pick up a developer's .env or real credentials.
os.environ.setdefault("MOCK_BEDROCK", "true")
os.environ.setdefault("MOCK_SNOWFLAKE", "true")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
