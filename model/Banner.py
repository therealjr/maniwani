import datetime
import io
import json
import mimetypes
import os
import subprocess
from PIL import Image, ImageDraw
from flask import send_from_directory, redirect, session, url_for
from shared import db, app


class Banner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ext = db.Column(db.String(4), nullable=False)
    mimetype = db.Column(db.String(255), nullable=False)
    is_animated = db.Column(db.Boolean, nullable=False)

    def delete_attachment(self):
        storage.delete_attachment(self.id, self.ext)


class StorageBase:
    _FFMPEG_FLAGS = "-i pipe:0 -f mjpeg -frames:v 1 -vf scale=w=500:h=500:force_original_aspect_ratio=decrease pipe:1"
    def save_attachment(self, attachment_file):
        file_ext = attachment_file.filename.rsplit(".", 1)[1].lower()
        media = Media(ext=file_ext, mimetype=attachment_file.content_type, is_animated=False)
        db.session.add(media)
        db.session.flush()
        media_id = media.id
        attachment_buffer = io.BytesIO(attachment_file.read())
        self._write_attachment(attachment_buffer, media_id, file_ext)
        thumbnail, is_animated = self._make_thumbnail(attachment_file,
                                                      media_id,
                                                      attachment_file.content_type, file_ext)
        media.is_animated = is_animated
        self._write_thumbnail(thumbnail, media_id)
        return media
    def bootstrap(self):
        pass
    def update(self):
        pass
    def static_resource(self, path):
        raise NotImplementedError
    def get_media_url(self, media_id, media_ext):
        return url_for("upload.file", media_id=media_id)
    def _write_attachment(self, attachment_file, media_id, media_ext):
        raise NotImplementedError
    def _get_ffmpeg_path(self):
        return app.config.get("FFMPEG_PATH") or "ffmpeg"



class FolderStorage(StorageBase):
    _ATTACHMENT_FILENAME = "%d.%s"
    _THUMBNAIL_FILENAME = "%d.jpg"
    def __init__(self):
        super().__init__()
        self._upload_folder = app.config["BANNER_FOLDER"]
    def get_attachment(self, media_id):
        media = db.session.query(Media).filter(Media.id == media_id).one()
        return send_from_directory(self._upload_folder,
                                   self._attachment_name(media.id, media.ext),
                                   last_modified=datetime.datetime.now())
    def delete_attachment(self, media_id, media_ext):
        os.remove(os.path.join(self._upload_folder,
                               self._attachment_name(media_id, media_ext)))
    def bootstrap(self):
        if not os.path.exists("banners"):
            os.makedirs("banners")

    def _attachment_name(self, media_id, media_ext):
        return self._ATTACHMENT_FILENAME % (media_id, media_ext)

    def _write_attachment(self, attachment_bytes, media_id, media_ext):
        full_path = os.path.join(self._upload_folder,
                                 self._attachment_name(media_id, media_ext))
        open(full_path, "wb").write(attachment_bytes.getvalue())

class S3Storage(StorageBase):
    _ATTACHMENT_KEY = "%d.%s"
    _ATTACHMENT_BUCKET = "attachments"
    _STATIC_DIR = "static"
    _CUSTOM_STATIC_DIR = "deploy-configs/custom-static"
    _STATIC_BUCKET = "static"
    _PUBLIC_READ_POLICY = json.dumps({"Version":"2012-10-17",
                                      "Statement":[
                                          {
                                              "Sid":"allpublic",
                                              "Effect":"Allow",
                                              "Principal":{"AWS": "*"},
                                              "Action": "s3:GetObject",
                                              "Resource": "arn:aws:s3:::%s/*"
                                          }
                                      ]})

    def __init__(self):
        self._endpoint = app.config["S3_ENDPOINT"]
        self._access_key = app.config["S3_ACCESS_KEY"]
        self._secret_key = app.config["S3_SECRET_KEY"]
        self._bucket_uuid = app.config.get("S3_UUID_PREFIX") or ""
        self._s3_client = boto3.resource("s3",
                                         endpoint_url=self._endpoint,
                                         aws_access_key_id=self._access_key,
                                         aws_secret_access_key=self._secret_key)

    def get_attachment(self, media_id):
        media = db.session.query(Media).filter(Media.id == media_id).one()
        media_ext = media.ext
        return redirect(self.get_media_url(media_id, media_ext))
    def get_media_url(self, media_id, media_ext):
        s3_key = self._s3_attachment_key(media_id, media_ext)
        return self._format_url(self._ATTACHMENT_BUCKET, s3_key)
    def delete_attachment(self, media_id, media_ext):
        s3_attach_key = self._s3_attachment_key(media_id, media_ext)
        self._s3_remove_key(self._ATTACHMENT_BUCKET, s3_attach_key)
    def bootstrap(self):
        attachment_bucket_name = self._bucket_uuid + self._ATTACHMENT_BUCKET
        self._s3_client.create_bucket(Bucket=attachment_bucket_name)
        static_bucket_name = self._bucket_uuid + self._STATIC_BUCKET
        self._s3_client.create_bucket(Bucket=static_bucket_name)
        self.update()
    def update(self):
        static_bucket = self._get_bucket(self._STATIC_BUCKET)
        # copy over all static files
        static_dirs = [self._STATIC_DIR]
        # check for custom static directory
        if os.path.exists(self._CUSTOM_STATIC_DIR):
            static_dirs.append(self._CUSTOM_STATIC_DIR)
        for static_dir in static_dirs:
            for base, _, filenames in os.walk(static_dir):
                for filename in filenames:
                    # strip the unecessary part of the path (static or deploy-configs/custom-static)
                    s3_key = "/".join([base[len(static_dir + "/"):], filename])
                    # correctly handle files at the root of the directory
                    if s3_key.startswith("/"):
                        s3_key = s3_key[1:]
                    full_path = os.path.join(static_dir, s3_key)
                    mimetype = self._get_mimetype(s3_key)
                    static_bucket.upload_file(full_path, s3_key, ExtraArgs={"ContentType": mimetype})
        for bucket_name in (self._ATTACHMENT_BUCKET, self._STATIC_BUCKET):
            bucket = self._get_bucket(bucket_name)
            formatted_policy = self._PUBLIC_READ_POLICY % (bucket.name)
            bucket.Policy().put(Policy=self._PUBLIC_READ_POLICY % bucket.name)
            bucket.Policy().reload()
    def static_resource(self, path):
        return self._format_url(self._STATIC_BUCKET, path)
    def _format_url(self, bucket, path):
        if app.config.get("CDN_REWRITE"):
            args = {
                "ENDPOINT": self._endpoint,
                "BUCKET_UUID": self._bucket_uuid,
                "BUCKET": bucket,
                "PATH": path}
            return app.config["CDN_REWRITE"].format(**args)
        else:
            return "%s/%s%s/%s" % (self._endpoint, self._bucket_uuid, bucket, path)
    def _get_bucket(self, bucket):
        return self._s3_client.Bucket(self._bucket_uuid + bucket)
    def _write_attachment(self, attachment_file, media_id, media_ext):
        s3_key = self._s3_attachment_key(media_id, media_ext)
        bucket = self._get_bucket(self._ATTACHMENT_BUCKET)
        mimetype = self._get_mimetype(s3_key)
        bucket.upload_fileobj(attachment_file, s3_key, ExtraArgs={"ContentType": mimetype})
    def _s3_remove_key(self, bucket_name, key):
        bucket = self._get_bucket(bucket_name)
        bucket.delete_objects(Delete={"Objects": [{"Key": key}]})
    def _s3_attachment_key(self, media_id, media_ext):
        return self._ATTACHMENT_KEY % (media_id, media_ext)
    def _get_mimetype(self, path):
        mimetype, _ = mimetypes.guess_type(path)
        return mimetype or "application/octet-stream"


def get_storage_provider():
    if app.config.get("STORAGE_PROVIDER") is None:
        return FolderStorage()
    if app.config["STORAGE_PROVIDER"] == "S3":
        # prevent non-s3 installations from needing to pull in boto3
        global boto3
        boto3 = __import__("boto3")
        return S3Storage()
    elif app.config["STORAGE_PROVIDER"] == "FOLDER":
        return FolderStorage()
    # TODO: proper error-handling on unknown key value
storage = get_storage_provider()


@app.context_processor
def static_handler():
    if app.config["SERVE_STATIC"]:
        def static_resource(path):
            return "/static/" + path
    else:
        def static_resource(path):
            return storage.static_resource(path)
    def get_current_theme():
        # TODO: extend to also keep theme preference in slips
        theme_name = session.get("theme")
        if theme_name is None:
            theme_name = app.config.get("DEFAULT_THEME") or "stock"
        return theme_name
    def get_current_theme_path():
        theme_name = get_current_theme()
        theme_template = "css/{THEME_NAME}/theme-{THEME_NAME}.css"
        return static_resource(theme_template.format(THEME_NAME=theme_name))
    def get_themes():
        return app.config.get("THEME_LIST") or ("stock", "harajuku", "wildride")
    return dict(static_resource=static_resource,
                get_current_theme=get_current_theme,
                get_current_theme_path=get_current_theme_path,
                get_themes=get_themes)


@app.context_processor
def upload_size():
    def max_upload_size():
        upload_byte_size = app.config["MAX_CONTENT_LENGTH"]
        megabyte_size = upload_byte_size / (1024 ** 2)
        return "%.1fMB" % megabyte_size
    return dict(max_upload_size=max_upload_size)


@app.context_processor
def upload_urls():
    return dict(get_media_url=storage.get_media_url)
