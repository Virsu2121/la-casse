# La Casse

Un lieu unique pour trouver de l'inspiration graphique : affiches, logos, tickets, badges, merch, signalétique, pochettes, maquettes et textures.

## Ce que contient le site

- **Galerie** : des images de vrais projets, tirées des flux publics des sites et de l'API d'Are.na. Elle est reconstruite chaque jour.
- **Collections** : une recherche dans les collections publiques d'Are.na.
- **Index** : plus de 25 000 titres et liens tirés des plans publics des sites.
- **Sites** : près de 100 liens classés par besoin.

## Droits

Ce dépôt ne contient aucune image ni aucun article des sites cités. Le site en ligne affiche les images depuis leur site d'origine, et chacune renvoie vers sa source. Elles restent la propriété de leurs auteurs.

Pour faire retirer une source ou une image, ouvre une [demande sur ce dépôt](../../issues).

## Utiliser la version complète sur son ordinateur

La version locale ajoute la lecture des articles dans un panneau et des vignettes dans l'index. Il faut Python 3 ; aucune dépendance à installer.

```bash
python serveur.py
```

Sous Windows, un double-clic sur `lancer.bat` fait la même chose.

## Fichiers

- `la-casse.html` : l'interface et l'index
- `serveur.py` : le lecteur local
- `construire_galerie.py` : construit `galerie.json` pour le site en ligne
- `.github/workflows/site.yml` : publication chaque jour sur GitHub Pages
