from flask import Blueprint, render_template, request, redirect, url_for, jsonify, session
from exts import db
from werkzeug.security import generate_password_hash, check_password_hash

bp = Blueprint('auth', __name__, url_prefix='/auth')