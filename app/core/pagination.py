from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel

MAX_PAGE_SIZE = 100


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int


class _PageParams(BaseModel):
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> _PageParams:
    return _PageParams(page=page, page_size=page_size)


PageParams = Annotated[_PageParams, Depends(_page_params)]
