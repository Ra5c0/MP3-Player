# 🎵 MP3 Player by Zunochikirin

Un lecteur MP3 moderne en **Python**, avec une interface élégante en **CustomTkinter** et un moteur audio **Pygame**.  
Ce projet permet de lire, gérer et organiser facilement vos morceaux de musique avec un système de playlists persistantes.

---

## 🖥️ Aperçu

![App Screenshot](assets/app.png)

Interface simple, sombre et fluide — tout est pensé pour une expérience musicale agréable et minimaliste.

---

## 🚀 Fonctionnalités principales

- 🎧 Lecture audio de fichiers `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`
- 🧩 Interface graphique avec **CustomTkinter**
- 🕹️ Commandes : lecture / pause / suivant / précédent / volume / progression
- 📁 Gestion des dossiers de musique
- 📝 Système de **playlists persistantes** via `playlists.json`
- 🧠 Affichage automatique des métadonnées (titre, artiste, durée)
- 🖼️ Icônes vectorielles (`.svg`) converties en PNG à la volée avec **CairoSVG**
- 💾 Sauvegarde automatique de la dernière lecture
- 🔊 Lecture fluide grâce à **Pygame mixer**

---

## 🧰 Technologies utilisées

| Composant | Description |
|------------|-------------|
| 🐍 Python 3.12+ | Langage principal |
| 🎨 CustomTkinter | Interface moderne et responsive |
| 🎵 Pygame | Gestion de la lecture audio |
| 🧮 Mutagen | Lecture des métadonnées (durée, tags) |
| 🖼️ Pillow | Gestion d’images et icônes |
| 🪄 CairoSVG *(optionnel)* | Conversion d’icônes SVG en PNG |

---

## ⚙️ Installation

### 1️⃣ Cloner le dépôt
```
git clone https://github.com/<votre_nom>/MP3-Player.git
cd MP3-Player
```

### 2️⃣ Créer un environnement virtuel
```
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
```

### 3️⃣ Installer les dépendances
```
pip install -r requirements.txt
```

### 4️⃣ Lancer l’application
```
python mp3_player.py
```

---

## 📂 Structure du projet

```
MP3-Player/
│
├── mp3_player.py          # Fichier principal (interface + logique)
├── playlists.json         # Données persistantes des playlists
├── requirements.txt       # Dépendances Python
│
├── assets/
│   ├── app.png            # Aperçu de l'application
│   └── app.ico            # Icône Windows
│
└── icons/                 # Icônes SVG utilisées dans l'interface
    ├── play.svg
    ├── pause.svg
    ├── add-folder.svg
    └── ...
```

---

## 🧑‍💻 Auteur

👤 **Oscar Gigon**
