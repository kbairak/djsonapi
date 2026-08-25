from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.models import User
from django.db import models

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import ManyRelatedManager


class Category(models.Model):
    id: int
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    articles: ManyRelatedManager["Article", "Article"]

    class Meta:
        verbose_name_plural = "categories"

    def __str__(self):
        return f"Category(id={self.id!r}, name={self.name!r})"


class Article(models.Model):
    id: int
    author_id: int
    title = models.CharField(max_length=200, unique=True)
    content = models.TextField()
    published = models.BooleanField(default=False)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="articles")
    created_at = models.DateTimeField(auto_now_add=True)
    categories = models.ManyToManyField(Category, related_name="articles")

    def __str__(self):
        return self.title
