# Generated by Django 3.1.1 on 2020-09-09 16:40

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0004_comment"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Comment",
        ),
    ]
