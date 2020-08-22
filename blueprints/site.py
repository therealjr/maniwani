import time

from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, session
from flask_restful import reqparse
from markdown import markdown
from werkzeug.http import parse_etags

import cache
from model.Board import Board
from model.Thread import Thread
from model.Slip import Slip
from model.Post import Post

from model.BoardList import BoardList
from model.Slip import get_slip, get_slip_bitmask
from shared import db, app

from sqlalchemy.sql import text

site_blueprint = Blueprint('site', __name__, template_folder='template')

@site_blueprint.route("/", methods=["GET", "POST"])
def index():
    if get_slip() and get_slip().is_admin:
        boards = db.session.query(Board).all()
        slips = db.session.query(Slip).all()
        if (request.method == "POST"):
            for slip in slips:
                admin_status = request.form.getlist(slip.name+"_grant_admin")
                mod_status = request.form.getlist(slip.name+"_grant_mod")
                revoke_slip = request.form.getlist(slip.name+"_revoke_slip")
                new_board_name = request.form.getlist("board_name")

                # editing mod/admin status
                if (len(admin_status) > 0 and admin_status[0] == "on"):
                    slip.is_admin = True
                else:
                    slip.is_admin = False

                if (len(mod_status) > 0 and mod_status[0] == "on"):
                    slip.is_mod = True
                else:
                    slip.is_mod = False

                old_slip = db.session.query(Slip).filter(Slip.name == slip.name).one()
                old_slip.is_admin = slip.is_admin
                old_slip.is_mod = slip.is_mod

                # revoking slip
                if (len(revoke_slip) > 0 and revoke_slip[0] == "on"):
                    db.session.query(Slip).filter(Slip.name == slip.name).delete()

            # add board
            if (len(new_board_name) > 0 and len(new_board_name[0]) > 0):
                # check to see that board already exists
                existing_boards = db.session.query(Board).filter(Board.name == new_board_name[0]).all()
                if (len(existing_boards) == 0):
                    new_board = Board(name=new_board_name[0], max_threads=50, mimetypes="", rules="", subreddits="")
                    db.session.add(new_board)


            # Delete board
            for board in boards:
                delete_board = request.form.getlist(board.name+"_delete_board")
                if (len(delete_board) > 0 and delete_board[0] == "on"):
                    boards_to_delete = db.session.query(Board).filter(Board.name == board.name).all()
                    for tmp_board in boards_to_delete:
                        threads_to_delete = db.session.query(Thread).filter(Thread.board == tmp_board.id).all()
                        for thread in threads_to_delete:
                            posts_to_delete = db.session.query(Post).filter(Post.thread == thread.id).all()
                            for post in posts_to_delete:
                                db.session.query(Post).filter(Post.id == post.id).delete()
                            db.session.query(Thread).filter(Thread.id == thread.id).delete()
                        db.session.query(Board).filter(Board.id == tmp_board.id).delete()
            db.session.commit()

            # edit FAQ
            faq = request.form.getlist("faq")
            if (len(faq) > 0):
                faq = faq[0]

            # edit rules
            rules = request.form.getlist("rules")
            if (len(rules) > 0):
                rules = rules[0]


        elif (request.method == "GET"):
            print("doing nothing")
        return render_template("site-admin.html", boards=boards, slips=slips, faq=render_template("faq.html"), rules=render_template("rules.html"))

    else:
        flash("Only admins can access board administration!")
        return redirect(url_for("main.faq"))
