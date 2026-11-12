from django.urls import path

from . import views


app_name = "assistant"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/documents/", views.documents_api, name="documents_api"),
    path("api/documents/upload/", views.upload_document, name="upload_document"),
    path("api/documents/<int:document_id>/", views.delete_document, name="delete_document"),
    path("api/documents/<int:document_id>/chunks/", views.document_chunks, name="document_chunks"),
    path("api/ask/", views.ask_question, name="ask_question"),
    path("api/quiz/", views.quiz_api, name="quiz_api"),
]
