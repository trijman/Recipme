from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
import json
import os
from werkzeug.utils import secure_filename
import docx
import pdfplumber
import hashlib
from datetime import timedelta, datetime
import markdown
from markupsafe import Markup
import re
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload



app = Flask(__name__)
app.secret_key = 'geheime_sleutel_voor_session'
app.permanent_session_lifetime = timedelta(days=30)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt', 'docx', 'pdf'}
ADMIN_EMAIL = "admin"
ADMIN_WACHTWOORD = "test"
GOOGLE_DRIVE_FOLDER_ID = "1BHkByvXM4JoMD3JrX2fsgUKPD3CMdLh3"

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    
# Drive functies

def get_drive_service():
    json_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "drive_service_account.json")
    credentials = service_account.Credentials.from_service_account_file(
        json_path,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=credentials)

def upload_to_drive(local_path, filename_on_drive, folder_id=None):
    service = get_drive_service()
    query = f"name = '{filename_on_drive}'"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    results = service.files().list(q=query, spaces='drive').execute()
    files = results.get('files', [])
    file_metadata = {'name': filename_on_drive}
    if folder_id:
        file_metadata['parents'] = [folder_id]
    media = MediaFileUpload(local_path, resumable=True)
    if files:
        file_id = files[0]['id']
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()

def download_from_drive(filename_on_drive, local_path, folder_id=None):
    service = get_drive_service()
    query = f"name = '{filename_on_drive}'"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    results = service.files().list(q=query, spaces='drive').execute()
    files = results.get('files', [])
    if not files:
        return False
    file_id = files[0]['id']
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return True

# Automatisch downloaden bij starten
for bestand in ["recepten.json", "instellingen.json", "favoriete_ingredienten.txt"]:
    download_from_drive(bestand, bestand, GOOGLE_DRIVE_FOLDER_ID)

# bestaande functies...
# in save_recipes(), save_settings() en favorieten toevoegen:

def save_recipes(recepten):
    with open('recepten.json', 'w', encoding='utf-8') as f:
        json.dump(recepten, f, indent=4, ensure_ascii=False)
    upload_to_drive("recepten.json", "recepten.json", GOOGLE_DRIVE_FOLDER_ID)

def save_settings(instellingen):
    with open('instellingen.json', 'w', encoding='utf-8') as f:
        json.dump(instellingen, f, indent=4, ensure_ascii=False)
    upload_to_drive("instellingen.json", "instellingen.json", GOOGLE_DRIVE_FOLDER_ID)

# in favoriete_ingredienten(): upload na schrijven
@app.route('/favorieten', methods=['GET', 'POST'])
@login_required
def favoriete_ingredienten():
    if request.method == 'POST':
        favorieten = request.form['favorieten']
        with open('favoriete_ingredienten.txt', 'w', encoding='utf-8') as f:
            f.write(favorieten)
        upload_to_drive("favoriete_ingredienten.txt", "favoriete_ingredienten.txt", GOOGLE_DRIVE_FOLDER_ID)
        return redirect(url_for('home'))
    else:
        try:
            with open('favoriete_ingredienten.txt', 'r', encoding='utf-8') as f:
                favorieten = f.read()
        except FileNotFoundError:
            favorieten = ''
        return render_template('favorieten.html', favorieten=favorieten)    

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_recipes():
    try:
        with open('recepten.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
        
def load_users():
    try:
        with open('gebruikers.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_users(users):
    with open('gebruikers.json', 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=4)

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'gebruiker' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
        


def load_settings():
    try:
        with open('instellingen.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "eenheden": ["gram", "kilogram", "eetlepel", "theelepel", "milliliter"],
            "soorten_gerechten": ["Lunch", "Diner", "Ontbijt"],
            "keukens": ["Thais", "Spaans", "Italiaans"],
            "standaard_weergave": "blok",
            "dark_mode": False
        }



@app.route('/')
@login_required
def home():
    recepten = load_recipes()
    instellingen = load_settings()
    instellingen['soorten'] = instellingen.get('soorten_gerechten', [])

    
    zoekterm = request.args.get('zoekterm', '').lower()
    geselecteerde_tag = request.args.get('tag', '').lower()
    weergave = request.args.get('weergave', instellingen.get('standaard_weergave', 'blok'))
    favorieten_filter = request.args.get('favorieten', 'alles')
    soort_filter = request.args.get('soort', '')
    keuken_filter = request.args.get('keuken', '')

    if zoekterm:
        recepten = [
            r for r in recepten
            if zoekterm in r['titel'].lower()
            or zoekterm in r.get('soort', '').lower()
            or any((ing.get('naam', '') if isinstance(ing, dict) else ing).lower().find(zoekterm) != -1 for ing in r.get('ingredienten', []))
        ]

    if geselecteerde_tag:
        recepten = [
            r for r in recepten
            if geselecteerde_tag in [tag.lower() for tag in r.get('tags', [])]
        ]

    if soort_filter:
        recepten = [r for r in recepten if r.get('soort', '') == soort_filter]

    if keuken_filter:
        recepten = [r for r in recepten if r.get('keuken', '') == keuken_filter]

    if favorieten_filter == 'favorieten':
        recepten = [r for r in recepten if r.get('favoriet')]

    return render_template(
        'home.html',
        recepten=recepten,
        zoekterm=zoekterm,
        weergave=weergave,
        favorieten_filter=favorieten_filter,
        instellingen=instellingen
    )



@app.route('/nieuw', methods=['GET', 'POST'])
@login_required
def nieuw_recept():
    instellingen = load_settings()
    if request.method == 'POST':
        titel = request.form['titel']
        personen = request.form.get('personen', '')
        soort = request.form.get('soort', '')
        keuken = request.form.get('keuken', '')
        tags_input = request.form.get('tags', '')

        # Verwerk tags (gescheiden door komma)
        tags = [tag.strip() for tag in tags_input.split(',') if tag.strip()]

        ingredienten = []
        aantallen = request.form.getlist('aantal')
        eenheden = request.form.getlist('eenheid')
        namen = request.form.getlist('naam')
        for aantal, eenheid, naam in zip(aantallen, eenheden, namen):
            if naam.strip():
                ingredienten.append({"aantal": aantal, "eenheid": eenheid, "naam": naam})

        bereidingswijze = request.form['bereidingswijze']

        recepten = load_recipes()
        nieuw = {
            'titel': titel,
            'personen': personen,
            'soort': soort,
            'keuken': keuken,
            'ingredienten': ingredienten,
            'bereidingswijze': bereidingswijze,
            'tags': tags,
            'favoriet': False,
            'laatst_bewerkt': datetime.now().isoformat()
        }
        recepten.append(nieuw)
        save_recipes(recepten)
        return redirect(url_for('home'))

    return render_template('nieuw.html', instellingen=instellingen)


@app.route('/recept/<int:index>')
@login_required
def recept_detail(index):
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recept = recepten[index]
        from markupsafe import Markup
        recept['bereidingswijze_html'] = Markup(recept['bereidingswijze'])
        return render_template('recept.html', recept=recept, index=index)
    else:
        return "Recept niet gevonden.", 404


@app.route('/verwijder/<int:index>', methods=['POST'])
@login_required
def verwijder_recept(index):
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recepten.pop(index)
        save_recipes(recepten)
        return redirect(url_for('home'))
    else:
        return "Recept niet gevonden.", 404

@app.route('/bewerk/<int:index>', methods=['GET', 'POST'])
@login_required
def bewerk_recept(index):
    recepten = load_recipes()
    instellingen = load_settings()

    if 0 <= index < len(recepten):
        recept = recepten[index]

        if request.method == 'POST':
            recept['titel'] = request.form['titel']
            recept['personen'] = request.form.get('personen', '')
            recept['soort'] = request.form.get('soort', '')
            recept['keuken'] = request.form.get('keuken', '')
            recept['link'] = request.form.get('link', '')

            tags_input = request.form.get('tags', '')
            tags = [tag.strip() for tag in tags_input.split(',') if tag.strip()]
            recept['tags'] = tags

            if recept.get('geimporteerd'):
                regels = request.form['ingredienten'].splitlines()
                recept['ingredienten'] = [{"naam": regel.strip()} for regel in regels if regel.strip()]
            else:
                ingredienten = []
                aantallen = request.form.getlist('aantal')
                eenheden = request.form.getlist('eenheid')
                namen = request.form.getlist('naam')
                for aantal, eenheid, naam in zip(aantallen, eenheden, namen):
                    if naam.strip():
                        ingredienten.append({"aantal": aantal, "eenheid": eenheid, "naam": naam})
                recept['ingredienten'] = ingredienten

            recept['bereidingswijze'] = request.form['bereidingswijze']
            recept['laatst_bewerkt'] = datetime.now().isoformat()

            save_recipes(recepten)
            return redirect(url_for('home'))

        return render_template('bewerken.html', recept=recept, instellingen=instellingen, index=index)

    else:
        return "Recept niet gevonden.", 404



@app.route('/print/<int:index>')
@login_required
def print_recept(index):
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recept = recepten[index]
        return render_template('print.html', recept=recept)
    else:
        return "Recept niet gevonden.", 404

@app.route('/favoriet/<int:index>', methods=['POST'])
@login_required
def toggle_favoriet(index):
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recepten[index]['favoriet'] = not recepten[index].get('favoriet', False)
        save_recipes(recepten)
        return redirect(url_for('home'))
    else:
        return "Recept niet gevonden.", 404

@app.route('/instellingen', methods=['GET', 'POST'])
@login_required
def instellingen():
    instellingen = load_settings()

    if request.method == 'POST':
        instellingen['eenheden'] = request.form.get('eenheden').splitlines()
        instellingen['soorten_gerechten'] = request.form.get('soorten_gerechten').splitlines()
        instellingen['keukens'] = request.form.get('keukens').splitlines()
        instellingen['standaard_weergave'] = request.form.get('standaard_weergave')
        instellingen['dark_mode'] = request.form.get('dark_mode') == 'aan'
        save_settings(instellingen)
        return redirect(url_for('home'))

    return render_template('instellingen.html', instellingen=instellingen)

@app.route('/favorieten', methods=['GET', 'POST'])
@login_required
def favoriete_ingredienten():
    if request.method == 'POST':
        favorieten = request.form['favorieten']
        with open('favoriete_ingredienten.txt', 'w', encoding='utf-8') as f:
            f.write(favorieten)
        upload_to_drive("favoriete_ingredienten.txt", "favoriete_ingredienten.txt", GOOGLE_DRIVE_FOLDER_ID)
        return redirect(url_for('home'))

    else:
        try:
            with open('favoriete_ingredienten.txt', 'r', encoding='utf-8') as f:
                favorieten = f.read()
        except FileNotFoundError:
            favorieten = ''
        return render_template('favorieten.html', favorieten=favorieten)

@app.route('/importeren', methods=['GET', 'POST'])
@login_required
def importeer_recept():
    instellingen = load_settings()
    tekst = ""

    if request.method == 'POST':
        if 'bestand' not in request.files:
            return redirect(request.url)

        bestand = request.files['bestand']

        if bestand.filename == '':
            return redirect(request.url)

        if bestand and allowed_file(bestand.filename):
            filename = secure_filename(bestand.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            bestand.save(filepath)

            ext = filename.rsplit('.', 1)[1].lower()

            if ext == 'txt':
                with open(filepath, 'r', encoding='utf-8') as f:
                    tekst = f.read()
            elif ext == 'docx':
                import docx
                doc = docx.Document(filepath)
                tekst = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            elif ext == 'pdf':
                import pdfplumber
                with pdfplumber.open(filepath) as pdf:
                    tekst = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())

            os.remove(filepath)

    return render_template('importeer.html', tekst=tekst, instellingen=instellingen)


@app.route('/opslaan_geimporteerd', methods=['POST'])
@login_required
def opslaan_geimporteerd():
    titel = request.form['titel']
    personen = request.form.get('personen', '')
    soort = request.form.get('soort', '')
    keuken = request.form.get('keuken', '')
    bereidingswijze = request.form['bereidingswijze']
    link = request.form.get('link', '')

    ingevoerde_ingredienten = request.form['ingredienten']
    regels = [regel.strip() for regel in ingevoerde_ingredienten.split('\n') if regel.strip()]
    ingredienten = [{"naam": regel} for regel in regels]

    nieuw_recept = {
        'titel': titel,
        'personen': personen,
        'soort': soort,
        'keuken': keuken,
        'ingredienten': ingredienten,
        'bereidingswijze': bereidingswijze,
        'favoriet': False,
        'link': link,
        'geimporteerd': True
    }

    recepten = load_recipes()
    recepten.append(nieuw_recept)
    save_recipes(recepten)

    return redirect(url_for('home'))


    
@app.route('/exporteer_boodschappenlijst/<int:index>/<int:personen>')
@login_required
def exporteer_boodschappenlijst(index, personen):
    import re
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recept = recepten[index]
        oorspronkelijk = int(recept.get('personen', 1)) or 1
        factor = personen / oorspronkelijk

        regels = [f"Boodschappenlijst voor {recept['titel']} ({personen} personen)", "\nIngrediënten:"]
        for ing in recept.get('ingredienten', []):
            if isinstance(ing, dict):
                try:
                    aantal = float(ing.get('aantal', 1)) * factor
                    aantal = int(aantal) if aantal.is_integer() else round(aantal, 2)
                    regels.append(f"- {aantal} {ing.get('eenheid', '')} {ing.get('naam', '')}")
                except (ValueError, TypeError):
                    regels.append(f"- {ing.get('naam', str(ing))}")
            else:
                regels.append(f"- {ing.get('naam', str(ing))}")


        regels.append("\nBereidingswijze:")
        html = recept.get('bereidingswijze', '')
        html = re.sub(r'</p>', '\n', html)
        html = re.sub(r'<br\s*/?>', '\n', html)
        plain_text = re.sub(r'<[^>]+>', '', html)
        regels.append(plain_text.strip())

        inhoud = "\n".join(regels)

        return Response(inhoud, mimetype='text/plain', headers={"Content-Disposition": f"attachment;filename={recept['titel']}_boodschappenlijst.txt"})
    else:
        return "Recept niet gevonden.", 404



@app.route('/boodschappenlijst/<int:index>', methods=['GET', 'POST'])
@login_required
def boodschappenlijst(index):
    recepten = load_recipes()
    if 0 <= index < len(recepten):
        recept = recepten[index]

        try:
            oorspronkelijk = int(recept.get('personen', 1))
        except (ValueError, TypeError):
            oorspronkelijk = 1

        if request.method == 'POST' and 'personen' in request.form:
            try:
                nieuw = int(request.form['personen'])
            except (ValueError, TypeError):
                nieuw = oorspronkelijk
        else:
            nieuw = oorspronkelijk

        factor = nieuw / oorspronkelijk if oorspronkelijk > 0 else 1

        geschaald = []
        schaalbaar = not recept.get('geimporteerd', False)  # Alleen schaalbaar als niet geïmporteerd

        for ing in recept.get('ingredienten', []):
            if isinstance(ing, dict) and 'aantal' in ing and 'naam' in ing:
                try:
                    aantal = float(ing.get('aantal', 1)) * factor
                    aantal = int(aantal) if aantal.is_integer() else round(aantal, 2)
                    geschaald.append({
                        'naam': ing.get('naam', ''),
                        'eenheid': ing.get('eenheid', ''),
                        'aantal': aantal
                    })
                except (ValueError, TypeError):
                    schaalbaar = False
                    break
            else:
                schaalbaar = False
                break

        if not schaalbaar:
            nieuw = oorspronkelijk
            geschaald = recept.get('ingredienten', [])

        return render_template('boodschappenlijst.html', recept=recept, ingredienten=geschaald, personen=nieuw, index=index, schaalbaar=schaalbaar)
    else:
        return "Recept niet gevonden.", 404


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        gebruikersnaam = request.form['gebruikersnaam']
        wachtwoord = request.form['wachtwoord']

        if gebruikersnaam == ADMIN_EMAIL and wachtwoord == ADMIN_WACHTWOORD:
            session['gebruiker'] = gebruikersnaam
            return redirect(url_for('home'))
        else:
            return render_template('login.html', foutmelding="Ongeldige inloggegevens.")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('gebruiker', None)
    return redirect(url_for('login'))

@app.route('/omrekenen', methods=['GET', 'POST'])
@login_required
def omrekenen():
    bestand = 'data/omrekenen.json'
    regels = []

    if request.method == 'POST':
        invoer_lijst = request.form.getlist('invoer')
        uitkomst_lijst = request.form.getlist('uitkomst')
        regels = [
            {'invoer': i.strip(), 'uitkomst': u.strip()}
            for i, u in zip(invoer_lijst, uitkomst_lijst)
            if i.strip() or u.strip()
        ]
        with open(bestand, 'w', encoding='utf-8') as f:
            json.dump({'regels': regels}, f, indent=2, ensure_ascii=False)

    elif os.path.exists(bestand):
        try:
            with open(bestand, 'r', encoding='utf-8') as f:
                regels = json.load(f).get('regels', [])
        except (json.JSONDecodeError, FileNotFoundError):
            regels = []

    return render_template('omrekenen.html', regels=regels)





if __name__ == '__main__':
    app.run(debug=True)