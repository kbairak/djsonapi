from .views import Article, Category, User


urlpatterns = (Article.as_views() +
               Category.as_views() +
               User.as_views())
