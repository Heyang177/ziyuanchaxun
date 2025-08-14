import csv
import logging
import os
import re
import urllib.parse
from datetime import datetime
from io import StringIO

import flask
import pymysql
from flask import Flask, render_template, request, session, redirect, url_for, Response, flash

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 创建Flask应用
app = flask.Flask(__name__)
# 从环境变量获取密钥，开发环境使用默认值
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key_here')
# 生产环境启用Secure Cookie
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('PRODUCTION', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 防止CSRF攻击
app.config['DEBUG'] = False

# 添加全局模板函数
app.jinja_env.globals.update(
    max=max,
    min=min
)

# 数据库配置
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': '123456',
    'database': 'ziyuan',
    'charset': 'utf8',
    'cursorclass': pymysql.cursors.DictCursor
}

# 自定义数据库连接管理类
class Database:
    def __init__(self):
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        try:
            self.connection = pymysql.connect(**DB_CONFIG)
            self.cursor = self.connection.cursor()
            return self.cursor
        except pymysql.Error as e:
            logging.error(f"数据库连接错误: {e}")
            raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            if self.connection:
                self.connection.rollback()
            logging.error(f"数据库操作错误: {exc_val}")
        else:
            if self.connection:
                self.connection.commit()
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()

# 安全响应头中间件
@app.after_request
def add_security_headers(response):
    # 安全头设置
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # 缓存控制（非静态资源）
    if not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
    # 简化Server头
    response.headers['Server'] = 'Web Server'
    
    return response

# 静态资源缓存策略
@app.after_request
def set_static_cache(response):
    if request.path.startswith('/static/'):
        # 静态资源缓存1年并设置immutable
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

# 登录页面
@app.route("/", methods=["GET", "POST"])
def login():
    # 清除用户会话信息
    session.pop('user', None)
    session.pop('is_admin', None)
    session['login'] = ''

    if request.method == 'POST':
        user = request.values.get("user", "").strip()
        pwd = request.values.get("pwd", "").strip()

        # 输入验证
        if not re.match(r"^[a-zA-Z0-9_]{3,20}$", user):
            return render_template('login.html', msg='用户名必须为3-20位字母、数字或下划线', user=user)
        
        if not re.match(r"^[a-zA-Z0-9!@#$%^&*()_+]{6,20}$", pwd):
            return render_template('login.html', msg='密码必须为6-20位字母、数字或特殊字符', user=user)

        # 使用上下文管理器管理数据库连接
        try:
            with Database() as cursor:
                sql = "SELECT * FROM admins WHERE admin_name=%s AND admin_password=%s;"
                cursor.execute(sql, (user, pwd))
                result = cursor.fetchone()

            if result:
                # 登陆成功，判断是否为管理员
                is_admin = user == 'admin'  # 假设admin用户为管理员
                session['login'] = 'OK'
                session['user'] = user
                session['is_admin'] = is_admin
                
                # 记录登录成功日志
                logging.info(f"用户 {user} 登录成功, 管理员: {is_admin}")
                
                # 生成绝对URL
                redirect_url = url_for('college_major', _external=True)
                logging.info(f"重定向URL: {redirect_url}")
                
                # 返回重定向响应
                return redirect(redirect_url)
            else:
                msg = '用户名或密码错误'
                logging.warning(f"用户 {user} 登录失败")
        except pymysql.Error as e:
            msg = '数据库操作错误，请稍后再试'
            logging.error(f"登录数据库错误: {e}")
        except Exception as e:
            msg = '系统错误，请联系管理员'
            logging.error(f"未知错误: {e}", exc_info=True)

    else:
        msg = ''
        user = ''

    return render_template('login.html', msg=msg, user=user)

# 院校专业查询页面
@app.route('/college_major', methods=['GET', 'POST'])
def college_major():
    # 会话验证并记录日志
    login_status = session.get("login", "")
    logging.info(f"访问college_major，会话状态: login={login_status}, user={session.get('user')}")
    
    if login_status != 'OK':
        logging.info("会话失效，重定向到登录页")
        return redirect('/')

    # 获取查询条件和分页参数
    current_page = request.values.get('page', 1, type=int)
    per_page = 20  # 每页显示数量

    # 处理GET和POST请求
    if request.method == 'POST':
        # 将POST请求转为带参数的GET请求，便于保持查询条件
        query_args = {
            'batch_name_query': request.form.get('batch_name_query', '').strip(),
            'college_code_query': request.form.get('college_code_query', '').strip(),
            'college_name_query': request.form.get('college_name_query', '').strip(),
            'major_code_query': request.form.get('major_code_query', '').strip(),
            'major_name_query': request.form.get('major_name_query', '').strip(),
            'subject_requirement_query': request.form.get('subject_requirement_query', '').strip(),
            'qualification_requirement_query': request.form.get('qualification_requirement_query', '').strip(),
            'page': current_page
        }
        return redirect(url_for('college_major', **query_args))
    else:
        batch_name_query = request.args.get('batch_name_query', '').strip()
        college_code_query = request.args.get('college_code_query', '').strip()
        college_name_query = request.args.get('college_name_query', '').strip()
        major_code_query = request.args.get('major_code_query', '').strip()
        major_name_query = request.args.get('major_name_query', '').strip()
        subject_requirement_query = request.args.get('subject_requirement_query', '').strip()
        qualification_requirement_query = request.args.get('qualification_requirement_query', '').strip()

    # 构建查询条件
    conditions = []
    values = []

    if batch_name_query:
        conditions.append('batch_name LIKE %s')
        values.append(f'%{batch_name_query}%')
    if college_code_query:
        conditions.append('college_code LIKE %s')
        values.append(f'%{college_code_query}%')
    if college_name_query:
        conditions.append('college_name LIKE %s')
        values.append(f'%{college_name_query}%')
    if major_code_query:
        conditions.append('major_code LIKE %s')
        values.append(f'%{major_code_query}%')
    if major_name_query:
        conditions.append('major_name LIKE %s')
        values.append(f'%{major_name_query}%')
    if subject_requirement_query:
        conditions.append('subject_requirement LIKE %s')
        values.append(f'%{subject_requirement_query}%')
    if qualification_requirement_query:
        conditions.append('qualification_requirement LIKE %s')
        values.append(f'%{qualification_requirement_query}%')

    # 计算总记录数
    total_count = 0
    total_pages = 1
    results = []
    query_result = "未查询到记录"

    try:
        with Database() as cursor:
            count_sql = "SELECT COUNT(*) as count FROM major_infos"
            if conditions:
                count_sql += " WHERE " + " AND ".join(conditions)

            cursor.execute(count_sql, tuple(values))
            result = cursor.fetchone()
            total_count = result['count'] if result else 0
            total_pages = max(1, (total_count + per_page - 1) // per_page)

            # 确保页码有效
            if current_page < 1:
                current_page = 1
            if current_page > total_pages:
                current_page = total_pages if total_pages > 0 else 1

            offset = (current_page - 1) * per_page

            # 查询数据
            data_sql = "SELECT * FROM major_infos"
            if conditions:
                data_sql += " WHERE " + " AND ".join(conditions)
            data_sql += " ORDER BY batch_name, college_code, major_code LIMIT %s OFFSET %s"

            query_values = values.copy()
            query_values.extend([per_page, offset])

            cursor.execute(data_sql, tuple(query_values))
            results = cursor.fetchall()

            if results:
                query_result = f"共查询到 {total_count} 条记录，显示第 {offset + 1} 到 {min(offset + per_page, total_count)} 条"
            else:
                query_result = "未找到符合条件的记录"

    except pymysql.Error as e:
        query_result = f"查询出错: {str(e)}"
        logging.error(f"查询错误: {e}")
        results = []
        total_pages = 1
        current_page = 1

    # 构建分页URL参数
    pagination_args = {
        'batch_name_query': batch_name_query,
        'college_code_query': college_code_query,
        'college_name_query': college_name_query,
        'major_code_query': major_code_query,
        'major_name_query': major_name_query,
        'subject_requirement_query': subject_requirement_query,
        'qualification_requirement_query': qualification_requirement_query
    }

    return render_template('college_major.html',
                           results=results,
                           query_result=query_result,
                           current_page=current_page,
                           total_pages=total_pages,
                           batch_name_query=batch_name_query,
                           pagination_args=pagination_args,
                           college_code_query=college_code_query,
                           college_name_query=college_name_query,
                           major_code_query=major_code_query,
                           major_name_query=major_name_query,
                           subject_requirement_query=subject_requirement_query,
                           qualification_requirement_query=qualification_requirement_query,
                           user_info=session.get('user', '未登录'),
                           is_admin=session.get('is_admin', False)
                           )

# 导出院校专业数据
@app.route('/export_college_major', methods=['GET'])
def export_college_major():
    """导出院校专业数据为CSV文件，支持中文标题和自动列宽"""
    if session.get("login", "") != 'OK':
        return redirect('/')

    # 获取查询条件
    batch_name_query = request.args.get('batch_name_query', '').strip()
    college_code_query = request.args.get('college_code_query', '').strip()
    college_name_query = request.args.get('college_name_query', '').strip()
    major_code_query = request.args.get('major_code_query', '').strip()
    major_name_query = request.args.get('major_name_query', '').strip()
    subject_requirement_query = request.args.get('subject_requirement_query', '').strip()
    qualification_requirement_query = request.args.get('qualification_requirement_query', '').strip()

    # 构建查询条件
    conditions = []
    values = []

    if batch_name_query:
        conditions.append('batch_name LIKE %s')
        values.append(f'%{batch_name_query}%')
    if college_code_query:
        conditions.append('college_code LIKE %s')
        values.append(f'%{college_code_query}%')
    if college_name_query:
        conditions.append('college_name LIKE %s')
        values.append(f'%{college_name_query}%')
    if major_code_query:
        conditions.append('major_code LIKE %s')
        values.append(f'%{major_code_query}%')
    if major_name_query:
        conditions.append('major_name LIKE %s')
        values.append(f'%{major_name_query}%')
    if subject_requirement_query:
        conditions.append('subject_requirement LIKE %s')
        values.append(f'%{subject_requirement_query}%')
    if qualification_requirement_query:
        conditions.append('qualification_requirement LIKE %s')
        values.append(f'%{qualification_requirement_query}%')

    try:
        with Database() as cursor:
            # 查询数据
            sql = "SELECT * FROM major_infos"
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY batch_name, college_code, major_code"

            cursor.execute(sql, tuple(values))
            results = cursor.fetchall()

        if not results:
            return "没有可导出的数据", 400

        # 定义中文标题映射
        header_map = {
            'batch_name': '批次名称',
            'college_code': '院校专业组代码',
            'college_name': '院校专业组名称',
            'major_code': '专业代号',
            'major_name': '专业名称',
            'subject_requirement': '选科要求',
            'qualification_requirement': '考生资格要求',
            'enrollment_number': '招生人数',
            'tuition_fee': '学费',
            'remarks': '备注'
        }

        # 创建CSV响应
        output = StringIO()
        output.write('\ufeff')  # 添加UTF-8 BOM

        # 获取原始字段名并映射为中文
        original_fields = list(results[0].keys()) if results else []
        chinese_fields = [header_map.get(field, field) for field in original_fields]

        # 创建CSV写入器
        writer = csv.DictWriter(
            output,
            fieldnames=original_fields,
            dialect='excel',
            quoting=csv.QUOTE_ALL
        )

        # 写入中文标题行
        writer.writerow({field: chinese_fields[i] for i, field in enumerate(original_fields)})

        # 写入数据行，处理中文编码
        for row in results:
            encoded_row = {}
            for key, value in row.items():
                if value is not None:
                    encoded_row[key] = str(value).encode('utf-8', 'replace').decode('utf-8')
                else:
                    encoded_row[key] = ''
            writer.writerow(encoded_row)

        output.seek(0)

        # 设置响应头
        today = datetime.now().strftime('%Y%m%d')
        filename = f'院校专业数据_{today}.csv'
        encoded_filename = urllib.parse.quote(filename, safe='')
        content_disposition = f'attachment; filename*=UTF-8\'\'{encoded_filename}'

        response = Response(
            output,
            mimetype='text/csv',  # 移除charset=utf-8
            headers={
                'Content-Disposition': content_disposition,
                'Content-Type': 'text/csv'  # 移除charset=utf-8
            }
        )

        return response

    except pymysql.Error as e:
        logging.error(f"导出错误: {e}")
        return f"导出失败: {str(e)}", 500

# 管理员控制面板
@app.route('/admin', methods=['GET'])
def admin_dashboard():
    """管理员控制面板"""
    if not (session.get("login") == 'OK' and session.get('is_admin', False)):
        return redirect('/')

    users = []
    try:
        with Database() as cursor:
            cursor.execute("SELECT id, admin_name FROM admins ORDER BY id")
            users = cursor.fetchall()
    except pymysql.Error as e:
        logging.error(f"获取用户列表错误: {e}")
        flash("获取用户列表失败", "danger")

    return render_template('admin.html', users=users, user_info=session.get('user'))

# 添加用户
@app.route('/admin/add_user', methods=['POST'])
def add_user():
    """添加新用户"""
    if not (session.get("login") == 'OK' and session.get('is_admin', False)):
        return redirect('/')

    username = request.form.get('admin_name', '').strip()
    password = request.form.get('admin_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()

    # 输入验证
    if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
        flash("用户名必须为3-20位字母、数字或下划线", "warning")
        return redirect(url_for('admin_dashboard'))

    if not re.match(r"^[a-zA-Z0-9!@#$%^&*()_+]{6,20}$", password):
        flash("密码必须为6-20位字母、数字或特殊字符", "warning")
        return redirect(url_for('admin_dashboard'))

    if password != confirm_password:
        flash("两次输入的密码不一致", "warning")
        return redirect(url_for('admin_dashboard'))

    # 检查用户名是否已存在
    try:
        with Database() as cursor:
            cursor.execute("SELECT id FROM admins WHERE admin_name=%s", (username,))
            if cursor.fetchone():
                flash(f"用户名 '{username}' 已存在", "warning")
                return redirect(url_for('admin_dashboard'))

            # 插入新用户
            cursor.execute(
                "INSERT INTO admins (admin_name, admin_password) VALUES (%s, %s)",
                (username, password)
            )
            flash(f"用户 '{username}' 添加成功", "success")

    except pymysql.Error as e:
        logging.error(f"添加用户错误: {e}")
        flash("添加用户失败", "danger")

    return redirect(url_for('admin_dashboard'))

# 修改用户
@app.route('/admin/edit_user/<int:user_id>', methods=['POST'])
def edit_user(user_id):
    """修改用户信息"""
    if not (session.get("login") == 'OK' and session.get('is_admin', False)):
        return redirect('/')

    new_username = request.form.get('admin_name', '').strip()

    # 输入验证
    if not re.match(r"^[a-zA-Z0-9_]{3,20}$", new_username):
        flash("用户名必须为3-20位字母、数字或下划线", "warning")
        return redirect(url_for('admin_dashboard'))

    # 检查用户名是否已存在
    try:
        with Database() as cursor:
            cursor.execute(
                "SELECT id FROM admins WHERE admin_name=%s AND id<>%s",
                (new_username, user_id)
            )
            if cursor.fetchone():
                flash(f"用户名 '{new_username}' 已存在", "warning")
                return redirect(url_for('admin_dashboard'))

            # 获取原用户名
            cursor.execute("SELECT admin_name FROM admins WHERE id=%s", (user_id,))
            user = cursor.fetchone()
            if not user:
                flash("用户不存在", "danger")
                return redirect(url_for('admin_dashboard'))

            # 更新用户名
            cursor.execute(
                "UPDATE admins SET admin_name=%s WHERE id=%s",
                (new_username, user_id)
            )
            flash(f"用户 '{user['admin_name']}' 已更新为 '{new_username}'", "success")

    except pymysql.Error as e:
        logging.error(f"修改用户错误: {e}")
        flash("修改用户失败", "danger")

    return redirect(url_for('admin_dashboard'))

# 修改用户密码
@app.route('/admin/change_password/<int:user_id>', methods=['POST'])
def change_password(user_id):
    """修改用户密码"""
    if not (session.get("login") == 'OK' and session.get('is_admin', False)):
        return redirect('/')

    new_password = request.form.get('new_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()

    # 输入验证
    if not re.match(r"^[a-zA-Z0-9!@#$%^&*()_+]{6,20}$", new_password):
        flash("密码必须为6-20位字母、数字或特殊字符", "warning")
        return redirect(url_for('admin_dashboard'))

    if new_password != confirm_password:
        flash("两次输入的密码不一致", "warning")
        return redirect(url_for('admin_dashboard'))

    try:
        with Database() as cursor:
            # 获取用户名
            cursor.execute("SELECT admin_name FROM admins WHERE id=%s", (user_id,))
            user = cursor.fetchone()
            if not user:
                flash("用户不存在", "danger")
                return redirect(url_for('admin_dashboard'))

            # 更新密码
            cursor.execute(
                "UPDATE admins SET admin_password=%s WHERE id=%s",
                (new_password, user_id)
            )
            flash(f"用户 '{user['admin_name']}' 的密码已更新", "success")

    except pymysql.Error as e:
        logging.error(f"修改密码错误: {e}")
        flash("修改密码失败", "danger")

    return redirect(url_for('admin_dashboard'))

# 删除用户
@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    """删除用户"""
    if not (session.get("login") == 'OK' and session.get('is_admin', False)):
        return redirect('/')

    try:
        with Database() as cursor:
            cursor.execute("SELECT admin_name FROM admins WHERE id=%s", (user_id,))
            user = cursor.fetchone()

        if not user:
            flash("用户不存在", "danger")
            return redirect(url_for('admin_dashboard'))

        # 防止删除当前登录用户
        if user['admin_name'] == session.get('user'):
            flash("不能删除当前登录的用户", "warning")
            return redirect(url_for('admin_dashboard'))

        with Database() as cursor:
            cursor.execute("DELETE FROM admins WHERE id=%s", (user_id,))
            flash(f"用户 '{user['admin_name']}' 已删除", "success")

    except pymysql.Error as e:
        logging.error(f"删除用户错误: {e}")
        flash("删除用户失败", "danger")

    return redirect(url_for('admin_dashboard'))

# 其他页面路由
@app.route('/college_query', methods=['GET', 'POST'])
def college_query():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('college_query.html', user_info=session.get('user'))

@app.route('/skill_college_score')
def skill_college_score():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('skill_college_score.html', user_info=session.get('user'))

@app.route('/hbea_embed')
def hbea_embed():
    return render_template('hbea_embed.html')

@app.route('/hubei_education_embed')
def hubei_education_embed():
    return render_template('hubei_education_embed.html')

@app.route('/hubei_zwfw_embed')
def hubei_zwfw_embed():
    return render_template('hubei_zwfw_embed.html')

@app.route('/chaxun')
def chaxun():
    return render_template('chaxun.html')

@app.route('/hbksw_embed_new')
def hbksw_embed_new():
    return render_template('hbksw_embed_new.html')

@app.route('/hbccks_embed')
def hbccks_embed():
    return render_template('hbccks_embed.html')

@app.route('/skill_college_1')
def skill_college_1():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('skill_college_1.html', user_info=session.get('user'))

@app.route('/hbksw_embed')
def hbksw_embed():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('hbksw_embed.html', user_info=session.get('user'))

@app.route('/skill_college_2')
def skill_college_2():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('skill_college_2.html', user_info=session.get('user'))

@app.route('/skill_college_3')
def skill_college_3():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('skill_college_3.html', user_info=session.get('user'))



@app.route('/score_rank', methods=['GET', 'POST'])
def score_rank():
    if session.get('login') != 'OK':
        return redirect('/')
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template('score_rank.html', msg='请输入姓名')
        try:
            with Database() as cursor:
                # 查询总分大于该用户的人数
                sql = "SELECT COUNT(*) as score_rank FROM score_table WHERE total_score > (SELECT total_score FROM score_table WHERE name = %s)" 
                cursor.execute(sql, (name,))
                result = cursor.fetchone()
                rank = result['score_rank'] + 1 if result else 1
                # 查询该用户总分
                sql = "SELECT theory_score, practical_score, cultural_score, total_score FROM score_table WHERE name = %s" 
                cursor.execute(sql, (name,))
                result = cursor.fetchone()
                theory_score = result['theory_score'] if result else 0
                practical_score = result['practical_score'] if result else 0
                cultural_score = result['cultural_score'] if result else 0
                total_score = result['total_score'] if result else 0
                return render_template('score_rank.html', name=name, rank=rank, theory_score=theory_score, practical_score=practical_score, cultural_score=cultural_score, total_score=total_score)
        except pymysql.Error as e:
            msg = '数据库操作错误，请稍后再试'
            logging.error(f"成绩排名查询数据库错误: {e}")
        except Exception as e:
            msg = '系统错误，请联系管理员'
            logging.error(f"成绩排名查询未知错误: {e}", exc_info=True)
    else:
        msg = ''
    return render_template('score_rank.html', msg=msg)

@app.route('/skill_college_4')
def skill_college_4():
    if session.get("login", "") != 'OK':
        return redirect('/')
    return render_template('skill_college_4.html', user_info=session.get('user'))

# 主程序入口
if __name__ == "__main__":
    # 生产环境建议使用Gunicorn等WSGI服务器，而非直接运行
    app.run(host='0.0.0.0', port=5000)