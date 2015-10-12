#!/usr/bin/python
# -*- coding: utf-8 -*-

# Import des librairies utilisees:
from qgis.core import *
from qgis.utils import iface
from PyQt4.QtCore import *
from PyQt4.QtGui import QFileDialog, QMessageBox
import os
import fnmatch
import shutil
import re
import csv
import codecs
import unicodedata
import time
import logging
import sys

# Definition de la classe de lecture d'un csv:
class TraitementFAM:
    def __init__(self):
        # supression des couches existantes dans le projet
        for layer in QgsMapLayerRegistry.instance().mapLayers():
            QgsMapLayerRegistry.instance().removeMapLayer(layer)

        # demande du repertoire (boite de dialogue)
        repSource = QFileDialog.getExistingDirectory(iface.mainWindow(), "Choix du repertoire des fichiers MapInfo")
        if repSource in (NULL, ""):
            return
        else:
            csvFile   = QFileDialog.getOpenFileName(iface.mainWindow(), "Choix du fichier csv contenant la liste blanche des appellations", "", "CSV File (*.csv)")
            if csvFile in (NULL, ""):
                return
            else:
                txt1 = "Traitement sur %s" % (repSource)
                txt2 = "Lancer le traitement sur %s avec la liste blanche %s ?" % (repSource, csvFile)
                res = QMessageBox.question(iface.mainWindow(), txt1, txt2, QMessageBox.Ok | QMessageBox.Cancel)
                if res != QMessageBox.Ok:
                    return

        self.source = repSource
        self.dir =  os.path.dirname(repSource)

        dateStr = time.strftime("%Y-%m-%d-%H-%M-%S")

        # Initialisation du timer:
        t0 = time.clock()
        # Mise en place du logger:
        self.initLogger(os.path.join(self.dir, "InfoLog-%s.csv" %(dateStr)))
        self.logger.info("TYPE;MESSAGE;FICHIER")
        # Recuperation de la liste blanche des appellations
        self.dictApp = self.getApp(csvFile)
        # Specification du systeme de projection
        self.crs = QgsCoordinateReferenceSystem(2154, QgsCoordinateReferenceSystem.PostgisCrsId)
        # Creation d'un dossier de travail unique
        self.workDir = createdir(os.path.join(self.dir, 'WorkDir'))
        # Traitement des couches
        featList = self.traitTabs()
        # On dedoublonne les parcelles validees avec ID UNI
        featList = self.dedoublListWithIDApp(featList)
        # On creee un shapefile unique pour y regrouper l'ensemble des features
        if len(featList) > 0:
            mergeName = os.path.join(self.dir, "Merge-%s.shp" %(dateStr))
            mergeCleanName = clean(mergeName)
            self.logger.info("I1;Merge des parcelles;%s" %(mergeCleanName))
            mergeFile = self.createMerge(mergeName)
            mergeLayer = self.load(mergeFile, mergeCleanName)
            mergeLayer = self.appendFeat(mergeLayer, featList, mergeCleanName)
        # On ferme le logger
        self.closeLogger()
        QMessageBox.information(iface.mainWindow(), "I2;Traitement effectue", "Traitement effectue en %s secondes" %(time.clock()-t0))

    # Fonction de fermeture du logger
    def closeLogger(self):
        handlers = self.logger.handlers[:]
        for handler in handlers:
            handler.close()
            self.logger.removeHandler(handler)

    # Fonction de parametrage du logger
    def initLogger(self, infoLog):
        formatter = logging.Formatter("%(asctime)s;%(levelname)s;%(message)s")
        handler_info = logging.FileHandler(infoLog, mode="w", encoding="utf-8")
        handler_info.setFormatter(formatter)
        handler_info.setLevel(logging.DEBUG)
        self.logger = logging.getLogger("TRAITEMENT_FAM")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(handler_info)

    # Fonction de chargement d'un layer:
    def load(self, tab, tabCleanName):
        try:
            layer = QgsVectorLayer(tab, tab, "ogr")
            layer.setCrs(self.crs)
            return layer
        except:
            self.logger.critical("C1;Chargement impossible;%s" %(tabCleanName))
            return NULL

    # Fonction de verification de la presence des champs INSEE, COMMUNE, NOM_AOC:
    def checkLayer(self, layer):
        # Recuperation de la liste des champs du layer
        fields = layer.pendingFields()
        # Initialisation:
        verif = ["INSEE","COMMUNE","NOM_AOC"]
        # On parcourt les champs a la recherche de INSEE, COMMUNE, NOM_AOC
        for field in fields:
            if field.name() == "INSEE":
                verif.remove("INSEE")
            if field.name() == "COMMUNE":
                verif.remove("COMMUNE")
            if field.name() == "NOM_AOC":
                verif.remove("NOM_AOC")
        return verif

    # Fonction de traitement principale:
    def traitTabs(self):
        inputSubFolders = self.listSubFolders()
        featList = []
        for folder in inputSubFolders:
            tabList = self.listFolderTabs(folder)
            self.logger.info(tabList)
            for tab in tabList:
                tabClean = clean(tab)
                tabCleanName = os.path.basename(os.path.splitext(tab)[0])
                # self.logger.info("I1;Debut de traitement de la table;%s" %(tabCleanName))
                try:
                    self.traitOneTab(tab, tabClean, tabCleanName, featList)
                except:
                    self.logger.critical("C2;Traitement impossible de la table;%s" %(tabCleanName))
                # self.logger.info("I1;Fin de traitement de la table;%s" %(tabCleanName))
        return featList

    # Fonction de traitement d'un tab:
    def traitOneTab(self, tab, tabClean, tabCleanName, featList):
        # Chargement du layer:
        layer = self.load(tab, tabCleanName)
        # Verification du layer si celui-ci a pu etre charge
        if layer != NULL:
            res = self.checkLayer(layer)
            # Si la verification echoue on log et on copie les fichiers dans le dossier d'erreurs
            if res:
                self.logger.critical("C4;Absence des champs %s;%s" %(res, tabCleanName))
            else:
                # On exporte le layer vers cet espace de travail en format shapefile
                shpSource = self.exportLayer(layer, "tmpShp", self.workDir)
                # On charge ce shapefile
                shpLayer = self.load(shpSource, tabCleanName)
                # Verification des capablities
                caps = shpLayer.dataProvider().capabilities()
                if not caps & QgsVectorDataProvider.ChangeAttributeValues or not caps & QgsVectorDataProvider.DeleteAttributes or not caps & QgsVectorDataProvider.AddAttributes:
                    self.logger.critical("C5;Modification impossible;%s"  %(tabCleanName))
                else:
                    # On supprime les champs inutiles
                    self.deleteFields(shpLayer)
                    # On ajoute les champs necessaires
                    fields = []
                    fields.append(QgsField("ID_UNI", QVariant.String, "string", 20))
                    fields.append(QgsField("SEGMENT", QVariant.String, "string", 20))
                    fields.append(QgsField("ID_APP", QVariant.String, "string", 20))
                    fields.append(QgsField("TAB", QVariant.String, "string", 100))
                    self.addField(shpLayer, fields)
                    # On rejette les features dont l'appellation ne fait pas partie de la liste blanche
                    tmpFeatList = self.calcField(tabClean, tabCleanName, shpLayer)
                    # On dedoublonne les parcelles validees:
                    tmpFeatList = self.dedoublList(tmpFeatList)
                    # On ajoute les parcelles a la liste globale
                    featList += tmpFeatList

    # Fonction de recuperation de la liste des sous-dossiers communaux:
    def listSubFolders(self):
        #Expression reguliere pour rechercher les dossiers par communes
        regex = re.compile(r"^[0-9]{5}")
        # Listage des sous-dossiers pour travailler au niveau des dossiers communaux
        inputSubFolders = [os.path.join(dirpath, os.path.basename(name))
            for dirpath, dirnames, files in os.walk(self.source)
            for name in dirnames
            if regex.match(name) ]
        return inputSubFolders

    # Fonction listant les fichiers .tab contenus dans un dossier:
    def listFolderTabs(self, folder):
        tabList = [os.path.join(dirpath, f)
            for dirpath, dirnames, files in os.walk(folder)
            for f in fnmatch.filter(files, '*.[Tt][Aa][Bb]')]
        return tabList

    # Fonction de creation d'un shapefile mergee par commune:
    def createMerge(self, outputFilename):
        fields = QgsFields()
        fields.append(QgsField("Id_uni", QVariant.String, "string", 20))
        fields.append(QgsField("Segment", QVariant.String, "string", 20))
        fields.append(QgsField("IDApp", QVariant.String, "string", 20))
        fields.append(QgsField("INSEE", QVariant.String, "string", 9))
        fields.append(QgsField("COMMUNE", QVariant.String, "string", 100))
        fields.append(QgsField("APPELLATIO", QVariant.String, "string", 254))
        fields.append(QgsField("TAB", QVariant.String, "string", 100))
        writer = QgsVectorFileWriter(outputFilename, "latin-1", fields,    QGis.WKBPolygon, self.crs, "ESRI Shapefile")
        return outputFilename

    # Fonction d'ajout d'un champ:
    def addField(self, layer, field):
        res = layer.dataProvider().addAttributes(field)
        layer.updateFields()

    # Fonction d'ajout d'une liste de features au shapefile merge:
    def appendFeat(self, layer, featureList, mergeCleanName):
        caps = layer.dataProvider().capabilities()
        if not caps & QgsVectorDataProvider.AddFeatures:
            self.logger.critical("C5;Ajout dans le merge impossible;%s" %(mergeCleanName))
        else:
            layer.startEditing()
            for f in featureList:
                newFeat = QgsFeature(layer.pendingFields())
                newFeat.setAttribute("Id_uni", f["ID_UNI"])
                newFeat.setAttribute("Segment", f["SEGMENT"])
                newFeat.setAttribute("IDApp", f["ID_APP"])
                newFeat.setAttribute("INSEE", f["INSEE"])
                newFeat.setAttribute("COMMUNE", f["COMMUNE"])
                newFeat.setAttribute("APPELLATIO", f["NOM_AOC"])
                newFeat.setAttribute("TAB", f["TAB"])
                newFeat.setGeometry(f.geometry())
                layer.dataProvider().addFeatures([newFeat])
            layer.commitChanges()
        return layer

    # Fonction de comparaison de l'appellation avec la liste blanche:
    def calcField(self, tabClean, tabCleanName, layer):
        index = layer.fieldNameIndex("NOM_AOC")
        layer.startEditing()
        featList = []
        # On boucle sur les features du layer communal:
        for feature in layer.getFeatures():
            # On verifie que la feature possede une geometrie:
            if feature.geometry() is not None:
                # On verifie que l'appellation est renseignee pour cette parcelle:
                if feature["NOM_AOC"]:
                    # On nettoye NOM_AOC (suppression des accents et caracteres speciaux):
                    nomAoc = removeAccents(feature["NOM_AOC"])
                    feature.setAttribute("NOM_AOC", nomAoc)
                    # On verifie que l'appellation fait partie de la liste blanche:
                    # Si l'appellation ne fait pas partie de la liste on ignore la parcelle:
                    if nomAoc not in self.dictApp and join(nomAoc) not in self.dictApp:
                        self.logger.error("E1;L'appellation %s ne fait pas partie de la liste blanche;%s" %(nomAoc, tabCleanName))
                    # Sinon on l'ajoute a la liste des features a ajouter au shapefile merge:
                    else:
                        idapps = self.dictApp[nomAoc]
                        segment = "1"
                        feature.setAttribute("ID_APP", idapps)
                        feature.setAttribute("ID_UNI", segment+'-'+idapps+'-'+feature["INSEE"])
                        feature.setAttribute("TAB", tabClean)
                        feature.setAttribute("SEGMENT", segment)
                        featList.append(feature)
                else:
                    self.logger.error("E2;L'appellation n'est pas renseignee;%s" %(tabCleanName))
            else:
                self.logger.error("E3;Geometrie absente;%s" %(tabCleanName))
        return featList

    # Fonction de suppresion des champs inutiles:
    def deleteFields(self, shpLayer):
        fields = shpLayer.pendingFields()
        delIndex = []
        for field in fields:
            if field.name() not in ("INSEE", "COMMUNE", "NOM_AOC"):
                fieldIndex = shpLayer.dataProvider().fieldNameIndex(field.name())
                delIndex.append(fieldIndex)
            else:
                pass
        res = shpLayer.dataProvider().deleteAttributes(delIndex)
        shpLayer.updateFields()
        return shpLayer

    # Fonction d'export vers shapefile:
    def exportLayer(self, layer, tabCleanName, comDir):
        filename = os.path.join(comDir,tabCleanName+'.shp')
        writer = QgsVectorFileWriter.writeAsVectorFormat(layer, filename, "latin-1",self.crs,"ESRI Shapefile")
        return filename

    # Fonction de creation du dictionnaire des appellations dans le csv ListeBlanche:
    def getApp(self, csvFile):
        dictApp = dict()
        with open(csvFile, 'rb') as f:
            r = CsvReader(f, delimiter=";", encoding='cp1252', quoting=csv.QUOTE_MINIMAL)
            for row in r:
                if len(row) >= 2:
                    nomAOC = removeAccents(row[1])
                    idApp = str(row[0])
                    # On ajout de nom AOC avec son ID App
                    dictApp[nomAOC] = idApp
                    # On ajout de nom AOC dont les mots sont joints (suppresion des espaces) avec son ID App (permet une recherche approchante)
                    dictApp[join(nomAOC)] = idApp
        f.close()
        return dictApp

    # Fonction permettant de dedoublonner les features d'un shapefile merge par commune sur la base de la geometrie (coordonnees du centroide et aire):
    def dedoublList(self, featureList):
        # On initialise une liste de cles uniques et une liste des features dedoublonnes:
        uniqueGeomList = []
        ddblFeatList = []
        for f in featureList:
            # On recupere pour chaque feature les valeurs de la geometrie(coordonnees du centroide et aire)
            area     = f.geometry().area()
            centroid = f.geometry().centroid().asPoint()
            x         = centroid.x()
            y         = centroid.y()
            # On les doublons
            matches = [v for v in uniqueGeomList if abs(area - v[0]) < 0.01 and abs(x - v[1]) < 0.01 and abs(y - v[2]) < 0.01]
            if len(matches) == 0:
                v = [area, x, y]
                uniqueGeomList.append(v)
                ddblFeatList.append(f)
        return ddblFeatList

    # Fonction permettant de dedoublonner les features d'un shapefile merge par commune sur la base de la geometrie (coordonnees du centroide et aire) et de l'ID App:
    def dedoublListWithIDApp(self, featureList):
        # On initialise une liste de cles uniques et une liste des features dedoublonnes:
        uniqueGeomList = []
        ddblFeatList = []
        for f in featureList:
            # On recupere pour chaque feature les valeurs de la geometrie(coordonnees du centroide et aire)
            area     = f.geometry().area()
            centroid = f.geometry().centroid().asPoint()
            x        = centroid.x()
            y        = centroid.y()
            idUni    = f["ID_UNI"]
            # On les doublons
            matches = [v for v in uniqueGeomList if abs(area - v[0]) < 0.01 and abs(x - v[1]) < 0.01 and abs(y - v[2]) < 0.01 and idUni == v[3]]
            if len(matches) == 0:
                v = [area, x, y, idUni]
                uniqueGeomList.append(v)
                ddblFeatList.append(f)
        return ddblFeatList

# Fonction de creation d'un dossier si celui-ci n'existe pas deja:
def createdir(x):
    if os.path.exists(x):
        shutil.rmtree(x, ignore_errors=False)
        os.makedirs(x)
    else:
        os.makedirs(x)
    return x

# Fonction permettant de nettoyer une chaine de caracteres en supprimant les accents, les caracteres speciaux et en la passant en majuscule:
def removeAccents(str):
    # Voir table ASCII cp1252 : http://www.ascii.ca/cp1252.htm
    str = str.lower()
    str = ''.join(['a' if ord(i) in (224, 225, 226, 227, 228, 229, 230) else i for i in str])
    str = ''.join(['e' if ord(i) in (232, 233, 234, 235) else i for i in str])
    str = ''.join(['i' if ord(i) in (237, 238, 239) else i for i in str])
    str = ''.join(['o' if ord(i) in (243, 244, 245) else i for i in str])
    str = ''.join(['u' if ord(i) in (250, 251, 252) else i for i in str])
    str = ''.join(['c' if ord(i) == 231 else i for i in str])
    str = ''.join([' ' if ord(i) <= 46 or (ord(i) >= 58 and ord(i) <= 64) or (ord(i) >= 58 and ord(i) <= 64) or (ord(i) >= 91 and ord(i) <= 96) or ord(i) >= 123 else i for i in str])
    str = str.upper()
    return ' '.join(str.split()).strip()

# Fonction permettant de nettoyer une chaine de caracteres en remplacant les caracteres non ascii par ?:
def clean(str):
    str = ''.join(['?' if ord(i) > 127 else i for i in str])
    return ' '.join(str.split()).strip()

# Fonction permettant de joindre les mots d'une chaine de caracteres:
def join(str):
    return ''.join(str.split())

# Definition de la classe de lecture d'un csv:
class CsvReader:
    def __init__(self, f, dialect=csv.excel, encoding="cp1252", **kwds):
        self.reader = csv.reader(f, dialect=dialect, **kwds)

    def next(self):
        row = self.reader.next()
        return [s for s in row]

    def __iter__(self):
        return self

# Fonction principale du programme:
def __main__():
    TraitementFAM()

__main__()
