from flask import Blueprint, render_template, request, flash, redirect, url_for
import logging

logger = logging.getLogger(__name__)

rnn_bp = Blueprint('rnn', __name__)

@rnn_bp.route('/rnn_data')
def rnn_data_page():
    """RNN模型数据管理页面"""
    return render_template('rnn/rnn_data.html')

@rnn_bp.route('/model_train')
def model_train_page():
    """模型训练页面"""
    return render_template('rnn/model_train.html')

@rnn_bp.route('/model_test')
def model_test_page():
    """模型测试页面"""
    return render_template('rnn/model_test.html') 