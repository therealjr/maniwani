from sqlalchemy import desc
from sqlalchemy.orm import relationship

from model.Thread import Thread
from shared import db


class BannerGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
