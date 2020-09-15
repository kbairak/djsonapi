from django.contrib.auth.models import User
from django.db import models


class Category(models.Model):
    slug = models.SlugField(unique=True)
    name = models.TextField()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "categories"


class Article(models.Model):
    slug = models.SlugField(unique=True)
    title = models.TextField()
    content = models.TextField()
    author = models.ForeignKey(User,
                               on_delete=models.CASCADE,
                               related_name="articles")
    categories = models.ManyToManyField(Category, related_name="articles")

    def __str__(self):
        return self.slug
