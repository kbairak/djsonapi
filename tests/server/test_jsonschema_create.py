import uuid
from typing import Annotated, ClassVar

from djsonapi.resource import Resource


def test_basic_create_schema():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title", "content"]
        _create_fields: ClassVar = ["title", "content"]
        _required_create_fields: ClassVar = ["title"]

        id: uuid.UUID
        title: str
        content: str

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_empty_create_fields():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title", "content"]
        _create_fields: ClassVar = []

        id: uuid.UUID
        title: str
        content: str

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
        },
        "required": ["type"],
        "additionalProperties": False,
    }


def test_create_with_client_id():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title", "content"]
        _create_fields: ClassVar = ["id", "title", "content"]
        _required_create_fields: ClassVar = ["id", "title"]

        id: uuid.UUID
        title: str
        content: str

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "id": {"type": ["string", "integer"]},
            "attributes": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "required": ["type", "id", "attributes"],
        "additionalProperties": False,
    }


def test_create_with_relationships():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title"]
        _singular_relationships: ClassVar = [("author", "users")]
        _create_fields: ClassVar = ["title", "author"]
        _required_create_fields: ClassVar = ["title"]

        id: uuid.UUID
        title: str
        author: uuid.UUID

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            "relationships": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"const": "users"},
                                    "id": {"type": "string"},
                                },
                                "required": ["type", "id"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_create_with_plural_relationships():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title"]
        _singular_relationships: ClassVar = [("author", "users")]
        _plural_relationships: ClassVar = ["tags"]
        _create_fields: ClassVar = ["title", "author", "tags"]
        _required_create_fields: ClassVar = ["title"]

        id: uuid.UUID
        title: str
        author: uuid.UUID
        tags: list[uuid.UUID]

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            "relationships": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"const": "users"},
                                    "id": {"type": "string"},
                                },
                                "required": ["type", "id"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                    "tags": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"const": "tags"},
                                        "id": {"type": "string"},
                                    },
                                    "required": ["type", "id"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_create_dict_relationships():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title"]
        _singular_relationships: ClassVar = {"author": "users"}
        _create_fields: ClassVar = ["title", "author"]

        id: uuid.UUID
        title: str
        author: uuid.UUID

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            "relationships": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"const": "users"},
                                    "id": {"type": "string"},
                                },
                                "required": ["type", "id"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_create_tuple_plural_relationships():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title"]
        _singular_relationships: ClassVar = ["author"]
        _plural_relationships: ClassVar = [("tags", "tag_objects"), "categories"]
        _create_fields: ClassVar = ["title", "author", "tags", "categories"]

        id: uuid.UUID
        title: str
        author: uuid.UUID
        tags: list[uuid.UUID]
        categories: list[uuid.UUID]

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            "relationships": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"const": "author"},
                                    "id": {"type": "string"},
                                },
                                "required": ["type", "id"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                    "tags": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"const": "tag_objects"},
                                        "id": {"type": "string"},
                                    },
                                    "required": ["type", "id"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                    "categories": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"const": "categories"},
                                        "id": {"type": "string"},
                                    },
                                    "required": ["type", "id"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_create_default_and_annotated():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title", "category"]
        _create_fields: ClassVar = ["title", "category"]
        _required_create_fields: ClassVar = ["title"]

        id: uuid.UUID
        title: str
        category: Annotated[str, {"examples": ["tech", "science"]}] = "tech"

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {
                        "type": "string",
                        "examples": ["tech", "science"],
                        "default": "tech",
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }


def test_create_no_relationships_no_attributes():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = []
        _singular_relationships: ClassVar = []
        _create_fields: ClassVar = []

        id: uuid.UUID

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
        },
        "required": ["type"],
        "additionalProperties": False,
    }


def test_create_only_relationships_no_attributes():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = []
        _singular_relationships: ClassVar = ["author"]
        _create_fields: ClassVar = ["author"]

        id: uuid.UUID
        author: uuid.UUID

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "relationships": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"const": "author"},
                                    "id": {"type": "string"},
                                },
                                "required": ["type", "id"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
        },
        "required": ["type"],
        "additionalProperties": False,
    }


def test_required_create_fields_implies_create_fields():
    class Article(Resource):
        _type: ClassVar = "articles"
        _attributes: ClassVar = ["title", "content"]
        _required_create_fields: ClassVar = ["title"]

        id: uuid.UUID
        title: str
        content: str

    assert Article.jsonschema_create() == {
        "type": "object",
        "properties": {
            "type": {"const": "articles"},
            "attributes": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "required": ["type", "attributes"],
        "additionalProperties": False,
    }
