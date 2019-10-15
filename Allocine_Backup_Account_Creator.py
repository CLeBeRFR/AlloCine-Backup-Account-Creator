import sys
import re
import os
# Vérification de la version de Python
if sys.version_info < (3, 7):
    print("La version de Python doit être supérieure à 3.7. Le programme ne peut pas continuer")
    exit(0)

# Vérification des bibliothèques nécessaires
try:
    from bs4 import BeautifulSoup
    import requests
except:
    print("Un ou plusieurs modules ne sont pas installés, l'installation va commencer")
    os.system("pip install beautifulsoup4")
    os.system("pip install requests")
    from bs4 import BeautifulSoup
    from bs4 import requests

# Vérification des paramètres
if len(sys.argv) != 2:
    print("Aucun argument passé en pramètre")
    print("Utilisation : python Allocine_Backup_Account_Creator.py <URL utilisateur>")
    exit(0)

# Récupération de l'identifiant utilisateur dans l'URL du profil
identifiant_utilisateur = re.search('membre-([A-Z]|[0-9])*', sys.argv[1]).group(0)
if "membre" not in identifiant_utilisateur:
    print("L'identifiant utilisateur n'a pas pu être récupéré, vérifiez le paramètre passé")
    print("Utilisation : python Allocine_Backup_Account_Creator.py <URL utilisateur>")
    exit(0)

def recuperer_notes(identifiant_utilisateur,type_media):
    print("Démarrage de la récupération des notes pour le type de média " + type_media)
    code_html = ""
    nombre_de_pages = 1

    # Récupération du nombre de pages liées au profil utilisateur
    if "page" in requests.get('http://www.allocine.fr/' + identifiant_utilisateur + '/' + type_media + '/?page=999').url:
        nombre_de_pages = int(requests.get(
            'http://www.allocine.fr/' + identifiant_utilisateur + '/' + type_media + '/?page=999').url.rsplit('=', 1)[
                1])
    print("nombre_de_pages : " + str(nombre_de_pages))
    for i in range(1, nombre_de_pages + 1):
        url = 'http://www.allocine.fr/' + identifiant_utilisateur + '/' + type_media + '/?page=' + str(i)
        code_html = code_html + requests.get(url).text

    # Parsing de la page HTML entière
    recherche_html = BeautifulSoup(code_html, 'html.parser')

    # Création du fichier de sorite
    liste_notes = open("liste_notes_" + type_media + ".csv", "w")
    liste_notes.write("Nom;Note" + os.linesep)
    # Pour chaque film
    for balise_film in recherche_html.find_all("div", class_="card entity-card-simple userprofile-entity-card-simple"):
        # Récupérer le nom du film
        nom_film = balise_film.find('img')['alt']
        # Compile l'expression régulière pour la classe de la note
        expression_reguliere_class = re.compile('rating-mdl n[0-5][0-5] stareval-stars')
        # Extrait la note de la classe
        note_film_html = balise_film.find("div", {"class": expression_reguliere_class})['class'][1][1:]
        note_film = (note_film_html[:1] + ',' + note_film_html[1:])
        liste_notes.write(nom_film + ";" + note_film + os.linesep)
    liste_notes.close()
    print("Fin de la récupération des notes pour le type de media " + type_media)

recuperer_notes(identifiant_utilisateur,"films")
recuperer_notes(identifiant_utilisateur, "series")
