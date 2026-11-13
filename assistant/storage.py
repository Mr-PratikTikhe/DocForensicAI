from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class VercelPrivateBlobStorage(Storage):
    """Django storage adapter for a Vercel private Blob store."""

    def _blob(self):
        from vercel.blob import sync as blob
        return blob

    def _open(self, name, mode="rb"):
        if mode not in {"rb", "r"}:
            raise ValueError("Vercel Blob storage only supports read access.")
        result = self._blob().get_blob(name, access="private")
        return ContentFile(result.body, name=name)

    def _save(self, name, content):
        content.open("rb")
        body = content.read()
        result = self._blob().put_blob(
            name,
            body,
            access="private",
            content_type=getattr(content, "content_type", None),
        )
        return result.pathname

    def delete(self, name):
        self._blob().delete_blob(name)

    def exists(self, name):
        try:
            self._blob().head_blob(name)
            return True
        except FileNotFoundError:
            return False

    def url(self, name):
        # Documents are served through an authenticated Django endpoint.
        raise ValueError("Private documents must be served through the application.")

    def path(self, name):
        raise NotImplementedError("Vercel Blob objects do not have local filesystem paths.")

    def size(self, name):
        return self._blob().head_blob(name).size

    def get_valid_name(self, name):
        return name
