from django.urls import path

from . import views


app_name = "assistant"
urlpatterns = [
    path("beta-login/", views.site_login, name="site_login"),
    path("beta-logout/", views.site_logout, name="site_logout"),
    path("demo/", views.demo_dashboard, name="demo_dashboard"),
    path("", views.dashboard, name="dashboard"),
    path("api/demo/documents/", views.demo_documents_api, name="demo_documents_api"),
    path("api/demo/documents/<int:document_id>/download/", views.demo_document_download, name="demo_document_download"),
    path("api/demo/documents/<int:document_id>/chunks/", views.demo_document_chunks, name="demo_document_chunks"),
    path("api/demo/ask/", views.demo_ask_question, name="demo_ask_question"),
    path("api/documents/", views.documents_api, name="documents_api"),
    path("api/documents/<int:document_id>/download/", views.document_download, name="document_download"),
    path("api/documents/upload/", views.upload_document, name="upload_document"),
    path("api/documents/<int:document_id>/", views.delete_document, name="delete_document"),
    path("api/documents/<int:document_id>/chunks/", views.document_chunks, name="document_chunks"),
    path("api/ask/", views.ask_question, name="ask_question"),
    path("api/quiz/", views.quiz_api, name="quiz_api"),
]
