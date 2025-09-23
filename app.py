from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import re
from datetime import datetime, timedelta
import google.generativeai as genai
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", 'your-super-secret-key-change-this-in-production')

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# Configure upload settings
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'certificates'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'additional'), exist_ok=True)

# PM Internship Scheme Knowledge Base
INTERNSHIP_CONTEXT = """
You are an AI assistant for the PM Internship Scheme, a Government of India initiative. Here's key information:

ELIGIBILITY CRITERIA:
- Age: 21-24 years
- Indian citizen with valid documents
- Not enrolled in full-time education during internship
- Not in full-time employment
- Family income less than ₹8 lakhs per annum
- No family member should have a government job

BENEFITS:
- Monthly stipend: ₹5,000 (₹4,500 from government + ₹500 from company)
- One-time grant: ₹6,000 for learning materials
- Health and accident insurance coverage
- Official internship certificate upon completion
- Industry exposure and mentorship

APPLICATION PROCESS:
1. Check eligibility criteria
2. Register on the official portal
3. Fill the application form completely
4. Upload required documents (Aadhaar, educational certificates, income certificate, bank details, photo)
5. Submit application
6. Track status in 'My Applications' section

DURATION: Typically 12 months, may vary by sector/company

SECTORS: IT & Technology, Healthcare, Finance & Banking, Manufacturing, Government Organizations

CONTACT SUPPORT:
- Email: contact-pminternship@gov.in
- Phone: 011-12345678 (10 AM - 6 PM, Monday-Friday)

Always be helpful, accurate, and professional. Keep responses concise but comprehensive. Use emojis appropriately.
"""

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database setup with migration support
def init_db():
    """Initialize the database with user table"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # Create users table if it doesn't exist
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')
    
    # Add columns for comprehensive form
    columns_to_add = [
        ('full_name', 'TEXT DEFAULT "User"'),
        ('father_name', 'TEXT'),
        ('gender', 'TEXT'),
        ('phone', 'TEXT'),
        ('otp_verified', 'BOOLEAN DEFAULT 0'),
        ('district', 'TEXT'),
        ('address', 'TEXT'),
        ('qualification', 'TEXT'),
        ('qualification_marks', 'REAL'),
        ('course', 'TEXT'),
        ('course_marks', 'REAL'),
        ('qualification_certificate', 'TEXT'),
        ('area_of_interest', 'TEXT'),
        ('skills', 'TEXT'),
        ('languages', 'TEXT'),
        ('additional_certificates', 'TEXT'),
        ('experience', 'TEXT'),
        ('prior_internship', 'TEXT'),
        ('internship_certificate', 'TEXT'),
        ('profile_completed', 'BOOLEAN DEFAULT 0'),
        ('registration_completed', 'BOOLEAN DEFAULT 0')
    ]
    
    migration_count = 0
    for column_name, column_type in columns_to_add:
        try:
            c.execute(f"SELECT {column_name} FROM users LIMIT 1")
        except sqlite3.OperationalError:
            c.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
            migration_count += 1
    
    # Only print migration message once if any columns were added
    if migration_count > 0:
        print(f"✅ Database migration completed: Added {migration_count} new columns")
    
    # Create chat logs table
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_message TEXT,
            bot_response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

def check_email_exists(email):
    """Check if email already exists in database"""
    conn = get_db_connection()
    existing_user = conn.execute(
        'SELECT id, email FROM users WHERE email = ?', (email.strip().lower(),)
    ).fetchone()
    conn.close()
    return existing_user is not None

def create_user(full_name, email, password):
    """Create a new user in the database with proper email validation"""
    try:
        # First check if email already exists
        if check_email_exists(email):
            return False, "Email already registered"
        
        conn = get_db_connection()
        password_hash = generate_password_hash(password)
        
        c = conn.cursor()
        c.execute(
            'INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)',
            (full_name.strip(), email.strip().lower(), password_hash)
        )
        conn.commit()
        conn.close()
        return True, "User created successfully"
        
    except sqlite3.IntegrityError:
        # This catches database-level unique constraint violations
        return False, "Email already registered"
    except Exception as e:
        print(f"Error creating user: {e}")
        return False, "Error creating account"

def verify_user(email, password):
    """Verify user credentials"""
    conn = get_db_connection()
    user = conn.execute(
        'SELECT * FROM users WHERE email = ?', (email,)
    ).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None

def update_last_login(user_id):
    """Update user's last login timestamp"""
    conn = get_db_connection()
    conn.execute(
        'UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?',
        (user_id,)
    )
    conn.commit()
    conn.close()

def validate_password(password):
    """Validate password strength"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain an uppercase letter"
    
    if not re.search(r'[a-z]', password):
        return False, "Password must contain a lowercase letter"
    
    if not re.search(r'\d', password):
        return False, "Password must contain a number"
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain a special character"
    
    return True, "Password is valid"

def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def get_user_initials(full_name):
    """Get user initials from full name"""
    if not full_name or full_name == 'User':
        return "U"
    
    names = full_name.strip().split()
    if len(names) >= 2:
        return (names[0][0] + names[-1][0]).upper()
    else:
        return names[0][0].upper()

def get_user_display_name(full_name, email):
    """Get display name from full name or email"""
    if full_name and full_name != 'User':
        return full_name
    else:
        return email.split('@')[0].title()

def get_gemini_response(user_message, user_name="User", user_email=""):
    """Get response from Google Gemini with PM Internship context"""
    try:
        full_prompt = f"""
        {INTERNSHIP_CONTEXT}
        
        The user's name is {user_name} and their email is {user_email}. 
        Address them personally when appropriate.
        
        User question: {user_message}
        
        Provide a helpful response about the PM Internship Scheme:
        """
        
        response = model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=400,
                temperature=0.7,
            )
        )
        
        ai_response = response.text.strip()
        return ai_response
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        return get_fallback_response(user_message)

def get_fallback_response(message):
    """Enhanced fallback responses"""
    message_lower = message.lower()
    
    if any(word in message_lower for word in ['hi', 'hello', 'hey', 'namaste']):
        return "👋 Hello! I'm your PM Internship Assistant. How can I help you today?"
    elif any(word in message_lower for word in ['apply', 'application', 'how to']):
        return "🎯 **Application Process:**\n1️⃣ Check eligibility criteria\n2️⃣ Register on portal\n3️⃣ Fill application form\n4️⃣ Upload documents\n5️⃣ Submit application\n\n📱 Visit the Apply section for detailed steps!"
    elif any(word in message_lower for word in ['eligible', 'eligibility', 'criteria']):
        return "✅ **Eligibility Checklist:**\n🔹 Age: 21-24 years\n🔹 Indian citizen\n🔹 Not in full-time work/education\n🔹 Family income < ₹8 lakhs\n🔹 No govt job in family"
    elif any(word in message_lower for word in ['stipend', 'benefit', 'salary', 'money']):
        return "💰 **Amazing Benefits:**\n💵 ₹5,000 monthly stipend\n🎁 ₹6,000 one-time grant\n🏥 Health insurance\n📜 Official certificate\n🌟 Industry mentorship"
    elif any(word in message_lower for word in ['help', 'support', 'contact']):
        return "📞 **Need Help?**\n📧 Email: contact-pminternship@gov.in\n☎️ Phone: 011-12345678\n🕒 Mon-Fri: 10 AM - 6 PM"
    else:
        return "🤖 I can help you with:\n🔹 Eligibility criteria\n🔹 Application process\n🔹 Benefits & stipend\n🔹 Required documents\n🔹 Contact support\n\nWhat would you like to know?"

def log_conversation(user_message, bot_response, user_id=None):
    """Log conversations for analytics"""
    try:
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO chat_logs (user_id, user_message, bot_response) VALUES (?, ?, ?)',
            (user_id, user_message, bot_response)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Logging error: {e}")

# Initialize database
init_db()

# Clear any stale flash messages on startup
@app.before_request
def clear_stale_flash_messages():
    """Clear flash messages for non-authenticated users"""
    if request.endpoint not in ['login', 'signup', 'logout', 'clear_session'] and not session.get('logged_in'):
        # Clear any flash messages if user is not logged in
        if '_flashes' in session:
            session.pop('_flashes', None)

# Routes
@app.route('/')
def index():
    """Default page - shows login form"""
    return render_template('login.html')

@app.route('/home')
def home():
    """Home page - only accessible after login"""
    if not session.get('logged_in'):
        flash('Please login to access the home page', 'error')
        return redirect(url_for('index'))
    
    user_name = session.get('user_name', 'User')
    user_email = session.get('user_email', '')
    user_initials = session.get('user_initials', 'U')
    
    return render_template('home.html', 
                         user_name=user_name,
                         user_email=user_email, 
                         user_initials=user_initials)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember')
        
        if not email or not password:
            flash('Please enter both email and password', 'error')
            return render_template('login.html')
        
        user = verify_user(email, password)
        
        if user:
            # Clear any existing flash messages before login success
            session.pop('_flashes', None)
            
            # Get the actual full name from database
            try:
                # Ensure we get the actual full_name from the database
                full_name = user['full_name'] if user['full_name'] and user['full_name'] != 'User' else get_user_display_name(None, user['email'])
            except (KeyError, TypeError):
                full_name = get_user_display_name(None, user['email'])
            
            # Store user information in session
            session['user_id'] = user['id']
            session['user_name'] = full_name
            session['user_email'] = user['email']
            session['user_initials'] = get_user_initials(full_name)
            session['logged_in'] = True
            
            update_last_login(user['id'])
            
            if remember:
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=30)
            
            # Show personalized welcome message
            flash(f'Welcome back, {full_name}!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Invalid email or password. Please try again.', 'error')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Clear any existing flash messages
        session.pop('_flashes', None)
        
        # Validation checks
        if not full_name or not email or not password or not confirm_password:
            flash('All fields are required', 'error')
            return render_template('signup.html')
        
        if len(full_name.strip()) < 2:
            flash('Full name must be at least 2 characters long', 'error')
            return render_template('signup.html')
        
        if not validate_email(email):
            flash('Please enter a valid email address', 'error')
            return render_template('signup.html')
        
        # Check if email already exists BEFORE other validations
        if check_email_exists(email):
            flash('This email is already registered. Please use a different email or try logging in.', 'error')
            return render_template('signup.html')
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('signup.html')
        
        is_valid, message = validate_password(password)
        if not is_valid:
            flash(message, 'error')
            return render_template('signup.html')
        
        # Create user with enhanced error handling
        success, message = create_user(full_name, email, password)
        if success:
            flash('Account created successfully! Please login with your credentials.', 'success')
            return redirect(url_for('login'))
        else:
            if "already registered" in message:
                flash('This email is already registered. Please use a different email or try logging in.', 'error')
            else:
                flash('Error creating account. Please try again.', 'error')
            return render_template('signup.html')
    
    return render_template('signup.html')

@app.route('/logout')
def logout():
    """Logout user and clear session"""
    # Clear all session data
    session.clear()
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('index'))

# COMPREHENSIVE PROFILE ROUTE WITH PROPER FLASH MESSAGE HANDLING
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    """Comprehensive profile and registration form"""
    if not session.get('logged_in'):
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        try:
            # Get all form data
            form_data = {
                'full_name': request.form.get('full_name'),
                'father_name': request.form.get('father_name'),
                'gender': request.form.get('gender'),
                'phone': request.form.get('phone'),
                'district': request.form.get('district'),
                'address': request.form.get('address'),
                'qualification': request.form.get('qualification'),
                'qualification_marks': request.form.get('qualification_marks'),
                'course': request.form.get('course'),
                'course_marks': request.form.get('course_marks'),
                'area_of_interest': request.form.get('area_of_interest'),
                'skills': request.form.get('skills'),
                'languages': request.form.get('languages'),
                'experience': request.form.get('experience'),
                'prior_internship': request.form.get('prior_internship'),
                'otp_verified': 1,
                'registration_completed': 1
            }
            
            # Handle file uploads
            file_paths = {}
            
            # Qualification certificate
            if 'qualification_certificate' in request.files:
                file = request.files['qualification_certificate']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filename = f"qual_cert_{session.get('user_id')}_{filename}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'certificates', filename)
                    file.save(filepath)
                    file_paths['qualification_certificate'] = f'/static/uploads/certificates/{filename}'
            
            # Additional certificates (multiple files)
            if 'additional_certificates' in request.files:
                files = request.files.getlist('additional_certificates')
                additional_paths = []
                for file in files:
                    if file and file.filename and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        filename = f"add_cert_{session.get('user_id')}_{filename}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'additional', filename)
                        file.save(filepath)
                        additional_paths.append(f'/static/uploads/additional/{filename}')
                if additional_paths:
                    file_paths['additional_certificates'] = json.dumps(additional_paths)
            
            # Internship certificate
            if 'internship_certificate' in request.files:
                file = request.files['internship_certificate']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filename = f"intern_cert_{session.get('user_id')}_{filename}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'certificates', filename)
                    file.save(filepath)
                    file_paths['internship_certificate'] = f'/static/uploads/certificates/{filename}'
            
            # Update database
            conn = get_db_connection()
            all_data = {**form_data, **file_paths}
            
            # Create dynamic update query
            update_fields = []
            update_values = []
            
            for key, value in all_data.items():
                if value is not None and value != '':
                    update_fields.append(f"{key} = ?")
                    update_values.append(value)
            
            if update_fields:
                update_query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?"
                update_values.append(session.get('user_id'))
                
                conn.execute(update_query, update_values)
                conn.commit()
            
            conn.close()
            
            # Update session
            if form_data['full_name']:
                session['user_name'] = form_data['full_name']
                session['user_initials'] = get_user_initials(form_data['full_name'])
            
            # Clear old flash messages and add new success message
            session.pop('_flashes', None)
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))
            
        except Exception as e:
            print(f"Profile update error: {e}")
            # Clear old flash messages and add error message
            session.pop('_flashes', None)
            flash('Error updating profile. Please try again.', 'error')
            return redirect(url_for('profile'))
    
    # GET request - just display the form
    conn = get_db_connection()
    user = conn.execute(
        'SELECT * FROM users WHERE id = ?',
        (session.get('user_id'),)
    ).fetchone()
    conn.close()
    
    if not user:
        return redirect(url_for('index'))
    
    user_dict = dict(user)
    
    return render_template('profile.html', 
                         user=user_dict,
                         user_name=session.get('user_name', 'User'))

@app.route('/chat', methods=['POST'])
def chat():
    """Handle chatbot messages with Gemini AI"""
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        user_name = session.get('user_name', 'User')
        user_email = session.get('user_email', '')
        
        bot_response = get_gemini_response(user_message, user_name, user_email)
        log_conversation(user_message, bot_response, session.get('user_id'))
        
        return jsonify({
            'reply': bot_response,
            'success': True
        })
    
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({
            'reply': '⚠️ I apologize, but I encountered an error. Please try again or contact support.',
            'success': False
        }), 500

# Clear session route for troubleshooting
@app.route('/clear-session')
def clear_session():
    """Clear all session data and flash messages"""
    session.clear()
    return redirect(url_for('index'))

# Debug route to check users (remove in production)
@app.route('/debug-users')
def debug_users():
    """Debug route to see all users (remove in production)"""
    if not app.debug:
        return "Not available in production"
    
    conn = get_db_connection()
    users = conn.execute('SELECT id, full_name, email, created_at FROM users').fetchall()
    conn.close()
    
    output = "<h2>Registered Users:</h2><ul>"
    for user in users:
        output += f"<li>ID: {user['id']}, Name: {user['full_name']}, Email: {user['email']}, Created: {user['created_at']}</li>"
    output += "</ul>"
    output += "<br><a href='/clear-session'>Clear Session</a> | <a href='/'>Home</a>"
    
    return output

if __name__ == '__main__':
    app.run(debug=True)
