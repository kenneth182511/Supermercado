from flask import Flask, render_template, request, redirect, url_for, session, Response
import pymysql
import time
import csv
import io

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura'

def get_db_connection():
    connection = pymysql.connect(
        host='localhost',
        user='root',
        password='1234',  # Contraseña configurada en MySQL
        database='supermercado_db',
        cursorclass=pymysql.cursors.DictCursor
    )
    return connection

# --- MÓDULO 0: LOGIN Y MENÚ ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['contraseña']
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE email = %s AND contraseña = %s AND estado = 'Activo'", (email, password))
            user = cursor.fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id_usuario']
            session['nombre_usuario'] = user['nombre_usuario']
            session['rol'] = user['rol']
            return redirect(url_for('menu'))
        else:
            return render_template('login.html', error="Credenciales inválidas o usuario inactivo.")
            
    return render_template('login.html')

@app.route('/menu')
def menu():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) as total FROM cliente")
        total_clientes = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM producto")
        total_productos = cursor.fetchone()['total']
        
        cursor.execute("SELECT IFNULL(SUM(total), 0) as total_ventas FROM historial_facturas")
        total_recaudo = cursor.fetchone()['total_ventas']

        cursor.execute("""
            SELECT p.nombre, SUM(d.cantidad) as total_vendidos 
            FROM detalle_venta d 
            JOIN producto p ON d.id_producto = p.id_producto 
            GROUP BY p.id_producto ORDER BY total_vendidos DESC LIMIT 5
        """)
        top_productos = cursor.fetchall()
    conn.close()
    
    return render_template('menu.html', rol=session['rol'], nombre=session['nombre_usuario'], 
                           c=total_clientes, p=total_productos, r=total_recaudo, top=top_productos)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- EXPORTAR CIERRE DE CAJA / CSV ---
@app.route('/exportar-ventas')
def exportar_ventas():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT h.codigo_factura, c.nombre as cliente, u.nombre_usuario as vendedor, h.fecha_venta, h.total 
            FROM historial_facturas h 
            JOIN cliente c ON h.id_cliente = c.id_cliente 
            JOIN usuarios u ON h.id_usuario = u.id_usuario ORDER BY h.id_factura DESC
        """)
        facturas = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Codigo Factura', 'Cliente', 'Vendedor', 'Fecha Venta', 'Total (COP)'])
    
    for f in facturas:
        writer.writerow([f['codigo_factura'], f['cliente'], f['vendedor'], f['fecha_venta'], f['total']])
        
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=cierre_caja_supermercado.csv"}
    )

# --- SEGURIDAD: CAMBIAR CONTRASEÑA ---
@app.route('/cambiar-password', methods=['GET', 'POST'])
def cambiar_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    mensaje = None
    error = None
    
    if request.method == 'POST':
        actual = request.form['password_actual']
        nueva = request.form['password_nueva']
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE id_usuario = %s AND contraseña = %s", (session['user_id'], actual))
            user = cursor.fetchone()
            
            if user:
                cursor.execute("UPDATE usuarios SET contraseña = %s WHERE id_usuario = %s", (nueva, session['user_id']))
                conn.commit()
                mensaje = "¡Contraseña actualizada con éxito!"
            else:
                error = "La contraseña actual es incorrecta."
        conn.close()
        
    return render_template('cambiar_password.html', mensaje=mensaje, error=error)

# --- MÓDULO 1: GESTIÓN DE CLIENTES ---
@app.route('/clientes')
def clientes():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM cliente")
        lista_clientes = cursor.fetchall()
        cursor.execute("SELECT pais, COUNT(*) as total FROM cliente GROUP BY pais")
        reporte_nacionalidad = cursor.fetchall()
    conn.close()
    return render_template('clientes.html', clientes=lista_clientes, reporte=reporte_nacionalidad)

@app.route('/cliente/agregar', methods=['POST'])
def agregar_cliente():
    nombre = request.form['nombre']
    email = request.form['email']
    telefono = request.form['telefono']
    direccion = request.form['direccion']
    ciudad = request.form['ciudad']
    pais = request.form['pais']
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO cliente (nombre, email, telefono, direccion, ciudad, pais) VALUES (%s, %s, %s, %s, %s, %s)", 
                       (nombre, email, telefono, direccion, ciudad, pais))
        conn.commit()
    conn.close()
    return redirect(url_for('clientes'))

@app.route('/cliente/eliminar/<int:id>')
def eliminar_cliente(id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM cliente WHERE id_cliente = %s", (id,))
        conn.commit()
    conn.close()
    return redirect(url_for('clientes'))

# --- MÓDULO 2: VENTAS, POS Y GESTIÓN DE PRODUCTOS ---
@app.route('/ventas', methods=['GET', 'POST'])
def ventas():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM cliente")
        clientes = cursor.fetchall()
        
        cursor.execute("""
            SELECT p.id_producto, p.nombre, p.precio, s.cantidad_actual, s.ubicacion, p.categoria 
            FROM producto p JOIN stock s ON p.id_producto = s.id_producto
        """)
        productos = cursor.fetchall()
        
        cursor.execute("""
            SELECT h.codigo_factura, c.nombre as cliente, u.nombre_usuario as vendedor, h.fecha_venta, h.total 
            FROM historial_facturas h 
            JOIN cliente c ON h.id_cliente = c.id_cliente 
            JOIN usuarios u ON h.id_usuario = u.id_usuario ORDER BY h.id_factura DESC
        """)
        facturas = cursor.fetchall()
    conn.close()
    
    if request.method == 'POST':
        id_cliente = request.form['id_cliente']
        id_producto = request.form['id_producto']
        cantidad_vendida = int(request.form['cantidad'])
        id_usuario = session['user_id']
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT s.cantidad_actual, p.precio FROM stock s JOIN producto p ON s.id_producto = p.id_producto WHERE s.id_producto = %s", (id_producto,))
            prod_info = cursor.fetchone()
            
            if not prod_info or prod_info['cantidad_actual'] < cantidad_vendida:
                conn.close()
                return "Error crítico: Stock insuficiente para completar la venta.", 400
                
            total_venta = float(prod_info['precio']) * cantidad_vendida
            
            if total_venta > 99000000:
                conn.close()
                return "Error: El total de la factura supera el límite permitido por transacción ($99,000,000).", 400
            
            codigo_fac = f"FAC-{int(time.time())}"
            
            cursor.execute("INSERT INTO historial_facturas (codigo_factura, id_cliente, id_usuario, total) VALUES (%s, %s, %s, %s)", 
                           (codigo_fac, id_cliente, id_usuario, total_venta))
            
            cursor.execute("SELECT LAST_INSERT_ID() as id")
            id_factura = cursor.fetchone()['id']
            
            cursor.execute("INSERT INTO detalle_venta (id_factura, id_producto, cantidad, subtotal) VALUES (%s, %s, %s, %s)", 
                           (id_factura, id_producto, cantidad_vendida, total_venta))
            
            cursor.execute("UPDATE stock SET cantidad_actual = cantidad_actual - %s WHERE id_producto = %s", 
                           (cantidad_vendida, id_producto))
            
            conn.commit()
        conn.close()
        return redirect(url_for('ventas'))
        
    return render_template('ventas.html', clientes=clientes, productos=productos, facturas=facturas)

@app.route('/producto/agregar', methods=['POST'])
def agregar_producto():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    nombre = request.form['nombre']
    precio = float(request.form['precio'])
    categoria = request.form['categoria']
    cantidad_inicial = int(request.form['cantidad_inicial'])
    ubicacion = request.form['ubicacion']
    
    # Generar código único automáticamente para evitar el error 1364 de MySQL
    codigo_producto = f"PRD-{int(time.time())}"
    
    if precio > 15000000 or precio < 0:
        return "Error: El precio no puede superar los $15,000,000.", 400
    if cantidad_inicial > 99999 or cantidad_inicial < 0:
        return "Error: El stock inicial excede el límite permitido (máx. 99,999 unidades).", 400
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO producto (codigo, nombre, precio, categoria) VALUES (%s, %s, %s, %s)", 
                       (codigo_producto, nombre, precio, categoria))
        cursor.execute("SELECT LAST_INSERT_ID() as id")
        id_producto = cursor.fetchone()['id']
        cursor.execute("INSERT INTO stock (id_producto, cantidad_actual, ubicacion) VALUES (%s, %s, %s)", (id_producto, cantidad_inicial, ubicacion))
        conn.commit()
    conn.close()
    return redirect(url_for('ventas'))

@app.route('/producto/actualizar', methods=['POST'])
def actualizar_producto():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    id_producto = request.form['id_producto']
    nuevo_precio = float(request.form['precio'])
    stock_a_sumar = int(request.form['stock_extra'])
    
    if nuevo_precio > 15000000 or nuevo_precio < 0:
        return "Error: El precio no puede superar los $15,000,000.", 400
    if stock_a_sumar > 99999 or stock_a_sumar < 0:
        return "Error: No puedes sumar más de 99,999 unidades de golpe.", 400
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT cantidad_actual FROM stock WHERE id_producto = %s", (id_producto,))
        stock_actual_db = cursor.fetchone()['cantidad_actual']
        
        if (stock_actual_db + stock_a_sumar) > 99999:
            conn.close()
            return f"Error: El stock total acumulado no puede superar las 99,999 unidades. (Actual en bodega: {stock_actual_db})", 400

        cursor.execute("UPDATE producto SET precio = %s WHERE id_producto = %s", (nuevo_precio, id_producto))
        cursor.execute("UPDATE stock SET cantidad_actual = cantidad_actual + %s WHERE id_producto = %s", (stock_a_sumar, id_producto))
        conn.commit()
    conn.close()
    return redirect(url_for('ventas'))

# --- MÓDULO 3: GESTIÓN DE USUARIOS ---
@app.route('/usuarios')
def usuarios():
    if 'user_id' not in session or session['rol'] != 'administrador':
        return redirect(url_for('menu'))
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM usuarios")
        lista_usuarios = cursor.fetchall()
    conn.close()
    return render_template('usuarios.html', usuarios=lista_usuarios)

@app.route('/usuario/agregar', methods=['POST'])
def agregar_usuario():
    if session.get('rol') != 'administrador':
        return redirect(url_for('menu'))
    nombre = request.form['nombre_usuario']
    email = request.form['email']
    contraseña = request.form['contraseña']
    rol = request.form['rol']
    estado = request.form['estado']
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO usuarios (nombre_usuario, email, contraseña, rol, estado) VALUES (%s, %s, %s, %s, %s)", 
                       (nombre, email, contraseña, rol, estado))
        conn.commit()
    conn.close()
    return redirect(url_for('usuarios'))

@app.route('/usuario/eliminar/<int:id>')
def eliminar_usuario(id):
    if session.get('rol') != 'administrador':
        return redirect(url_for('menu'))
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM usuarios WHERE id_usuario = %s", (id,))
        conn.commit()
    conn.close()
    return redirect(url_for('usuarios'))

if __name__ == '__main__':
    app.run(debug=True)