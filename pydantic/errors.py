"""
Pydantic errors.
"""
from __future__ import annotations as _annotations

import re

from typing_extensions import Literal

from ._migration import getattr_migration

__all__ = (
    'PydanticUserError',
    'PydanticUndefinedAnnotation',
    'PydanticImportError',
    'PydanticSchemaGenerationError',
    'PydanticInvalidForJsonSchema',
)

# TODO set up a cloudflare worker to redirect to the correct page
# We use this URL to allow for future flexibility about how we host the docs, while allowing for Pydantic
# code in the while with "old" URLs to still work.
# 'u' refers to "user errors" - e.g. errors caused by developers using pydantic, as opposed to validation errors.
# DEV_ERROR_DOCS_URL = f'https://errors.pydantic.dev/{VERSION}/u/'
DEV_ERROR_DOCS_URL = '<TODO: Set up the errors URLs>/'
PydanticErrorCodes = Literal[
    'decorator-missing-field',
    'dataclass-not-fully-defined',
    'discriminator-no-field',
    'discriminator-alias-type',
    'discriminator-needs-literal',
    'discriminator-alias',
    'typed-dict-version',
    'model-field-overridden',
    'model-field-missing-annotation',
    'model-not-fully-defined',
    'config-both',
    'deprecated_kwargs',
    'invalid-for-json-schema',
    'json-schema-already-used',
    'base-model-instantiated',
    'undefined-annotation',
    'schema-for-unknown-type',
    'import-error',
    'create-model-field-definitions',
    'create-model-config-base',
    'validator-no-fields',
    'validator-invalid-fields',
    'validator-instance-method',
    'root-validator-pre-skip',
    'model-serializer-instance-method',
    'validator-field-config-info',
    'validator-v1-signature',
    'field-validator-signature',
    'field-serializer-signature',
    'model-serializer-signature',
    'multiple-field-serializers',
]


class PydanticErrorMixin:
    """
    A mixin class for common functionality shared by all Pydantic-specific errors.

    Attributes:
        message (str): A message describing the error.
        code (PydanticErrorCodes | None): An optional error code from PydanticErrorCodes enum.
    """

    def __init__(self, message: str, *, code: PydanticErrorCodes | None) -> None:
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if self.code is None:
            return self.message
        else:
            return f'{self.message}\n\nFor further information visit {DEV_ERROR_DOCS_URL}{self.code}'


class PydanticUserError(PydanticErrorMixin, TypeError):
    """
    Error raised due to incorrect use of Pydantic.
    """


class PydanticUndefinedAnnotation(PydanticErrorMixin, NameError):
    """A subclass of `NameError` raised when handling undefined annotations during `CoreSchema` generation.

    Attributes:
        name (str): Name of the error.
        message (str): Description of the error.
    """

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(message=message, code='undefined-annotation')

    @classmethod
    def from_name_error(cls, name_error: NameError) -> PydanticUndefinedAnnotation:
        """
        Convert a `NameError` to a `PydanticUndefinedAnnotation` error.

        Args:
            name_error (NameError): `NameError` to be converted.

        Returns:
            PydanticUndefinedAnnotation: Converted `PydanticUndefinedAnnotation` error.
        """
        try:
            name = name_error.name  # type: ignore  # python > 3.10
        except AttributeError:
            name = re.search(r".*'(.+?)'", str(name_error)).group(1)  # type: ignore[union-attr]
        return cls(name=name, message=str(name_error))


class PydanticImportError(PydanticErrorMixin, ImportError):
    """Error occurs when an import fails due to module changes between V1 and V2."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code='import-error')


class PydanticSchemaGenerationError(PydanticUserError):
    """
    Error raised during failures to generate a `CoreSchema` for some type.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code='schema-for-unknown-type')


class PydanticInvalidForJsonSchema(PydanticUserError):
    """
    Error raised during failures to generate a JSON schema for some `CoreSchema`.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code='invalid-for-json-schema')


__getattr__ = getattr_migration(__name__)
