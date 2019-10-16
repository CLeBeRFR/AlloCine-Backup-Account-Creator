# AlloCine-Backup-Account-Creator
Ce projet a pour but de permettre à un utilisateur de sauvegarder ses notes de films et séries notés sur le site internet AlloCiné

## Pourquoi ce projet ?
J'ai créé ce projet car AlloCiné ne permet pas d'extraire dans un format spécifique toutes ses notes attribuées aux différents films et séries. Le script permet donc de sauvegarder ses notes régulièrement si jamais AlloCiné venait à perdre les données des utilisateurs.

## Comment l'utiliser ?
L'installation de python en version 3.7 au minimum est nécessaire.<br />
`Allocine_Backup_Account_Creator.py <URL du profil utilisateur>`<br />
Le script créera deux fichiers *liste_notes_films.csv* et *liste_notes_series.csv* qui contiennent le nom du film ou série et sa note associée.
La sortie console sera du type :<br />
```python
Démarrage de la récupération des notes pour le type de média films
Nombre de pages : 18
Fin de la récupération des notes, 642 films sauvegardés
Démarrage de la récupération des notes pour le type de média series
Nombre de pages : 1
Fin de la récupération des notes, 19 series sauvegardés
```
