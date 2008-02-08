# Copyright (c) 2006-2007 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.

from conary import files, trove, versions
from conary import errors as conaryerrors
from conary.build import build, source
from conary.build import errors as builderrors
from conary.build.packagerecipe import AbstractPackageRecipe
from conary.lib import log, util
from conary.repository import changeset, filecontents

class DerivedPackageRecipe(AbstractPackageRecipe):

    internalAbstractBaseClass = 1
    _isDerived = True
    parentVersion = None

    def _expandChangeset(self):
        destdir = self.macros.destdir

        ptrMap = {}
        byDefault = {}

        fileList = []
        linkGroups = {}
        linkGroupFirstPath = {}
        # sort the files by pathId,fileId
        for trvCs in self.cs.iterNewTroveList():
            trv = trove.Trove(trvCs)

            # these should all be the same anyway
            flavor = trv.getFlavor().copy()
            name = trv.getName()
            self._componentReqs[name] = trv.getRequires().copy()
            self._componentProvs[name] = trv.getProvides().copy()

            for pathId, path, fileId, version in trv.iterFileList():
                if path != self.macros.buildlogpath:
                    fileList.append((pathId, fileId, path, name))

            if trv.isCollection():
                # gather up existing byDefault status
                # from (component, byDefault) tuples
                byDefault.update(dict(
                    [(x[0][0], x[1]) for x in trv.iterTroveListInfo()]))

        fileList.sort()

        restoreList = []

        for pathId, fileId, path, troveName in fileList:
            fileCs = self.cs.getFileChange(None, fileId)
            fileObj = files.ThawFile(fileCs, pathId)
            self._derivedFiles[path] = fileObj.inode.mtime()

            flavor -= fileObj.flavor()
            self._componentReqs[troveName] -= fileObj.requires()
            self._componentProvs[troveName] -= fileObj.requires()

            # Config vs. InitialContents etc. might be change in derived pkg
            # Set defaults here, and they can be overridden with
            # "exceptions = " later
            if fileObj.flags.isConfig():
                self.Config(path)
            elif fileObj.flags.isInitialContents():
                self.InitialContents(path)
            elif fileObj.flags.isTransient():
                self.Transient(path)


            # we don't restore setuid/setgid bits into the filesystem
            if fileObj.inode.perms() & 06000 != 0:
                self.SetModes(path, fileObj.inode.perms())

            if isinstance(fileObj, files.DeviceFile):
                self.MakeDevices(path, fileObj.lsTag,
                                 fileObj.devt.major(), fileObj.devt.minor(),
                                 fileObj.inode.owner(), fileObj.inode.group(),
                                 fileObj.inode.perms())
            elif fileObj.hasContents:
                restoreList.append((pathId, fileId, fileObj, destdir, path))
            else:
                fileObj.restore(None, destdir, destdir + path)

            if isinstance(fileObj, files.Directory):
                # remember to include this directory in the derived package
                self.ExcludeDirectories(exceptions = path)
            if isinstance(fileObj, files.SymbolicLink):
                # mtime for symlinks is meaningless, we have to record the
                # target of the symlink instead
                self._derivedFiles[path] = fileObj.target()

        delayedRestores = {}
        for pathId, fileId, fileObj, root, destPath in restoreList:
            (contentType, contents) = \
                            self.cs.getFileContents(pathId, fileId)
            if contentType == changeset.ChangedFileTypes.ptr:
                targetPtrId = contents.get().read()
                l = delayedRestores.setdefault(targetPtrId, [])
                l.append((root, fileObj, destPath))
                continue

            assert(contentType == changeset.ChangedFileTypes.file)

            ptrId = pathId + fileId
            if pathId in delayedRestores:
                ptrMap[pathId] = destPath
            elif ptrId in delayedRestores:
                ptrMap[ptrId] = destPath

            fileObj.restore(contents, root, root + destPath)

            linkGroup = fileObj.linkGroup()
            if linkGroup:
                linkGroups[linkGroup] = destPath

        for targetPtrId in delayedRestores:
            for root, fileObj, targetPath in delayedRestores[targetPtrId]:
                linkGroup = fileObj.linkGroup()
                if linkGroup in linkGroups:
                    util.createLink(destdir + linkGroups[linkGroup],
                                    destdir + targetPath)
                else:
                    sourcePath = ptrMap[targetPtrId]
                    fileObj.restore(
                        filecontents.FromFilesystem(root + sourcePath),
                        root, root + targetPath)

                    if linkGroup:
                        linkGroups[linkGroup] = targetPath

        self.useFlags = flavor

        self.setByDefaultOn(set(x for x in byDefault if byDefault[x]))
        self.setByDefaultOff(set(x for x in byDefault if not byDefault[x]))

    def unpackSources(self, resume=None, downloadOnly=False):

        repos = self.laReposCache.repos
        if self.parentVersion:
            try:
                parentRevision = versions.Revision(self.parentVersion)
            except conaryerrors.ParseError, e:
                raise builderrors.RecipeFileError(
                            'Cannot parse parentVersion %s: %s' % \
                                    (self.parentVersion, str(e)))
        else:
            parentRevision = None

        sourceBranch = versions.VersionFromString(self.macros.buildbranch)
        if not sourceBranch.isShadow():
            raise builderrors.RecipeFileError(
                    "only shadowed sources can be derived packages")

        if parentRevision and \
                self.sourceVersion.trailingRevision().getVersion() != \
                                                parentRevision.getVersion():
            raise builderrors.RecipeFileError(
                    "parentRevision must have the same upstream version as the "
                    "derived package recipe")

        # find all the flavors of the parent
        parentBranch = sourceBranch.parentBranch()

        if parentRevision:
            parentVersion = parentBranch.createVersion(parentRevision)
        else:
            parentVersion = parentBranch
        try:
            troveList = repos.findTrove(None, 
                                   (self.name, parentVersion, self._buildFlavor))
        except conaryerrors.TroveNotFound, err:
            raise builderrors.RecipeFileError('Could not find package to derive from for this flavor: ' + str(err))
        if len(troveList) > 1:
            raise builderrors.RecipeFileError(
                    'Multiple flavors of %s=%s match build flavor %s' \
                    % (self.name, parentVersion, self.cfg.buildFlavor))
        parentFlavor = troveList[0][2]
        parentVersion = troveList[0][1]

        log.info('deriving from %s=%s[%s]', self.name, parentVersion,
                 parentFlavor)

        # Fetch all binaries built from this source
        v = parentVersion.getSourceVersion(removeShadows=False)
        binaries = repos.getTrovesBySource(self.name + ':source', v)

        # Filter out older ones
        binaries = [ x for x in binaries \
                        if (x[1], x[2]) == (parentVersion,
                                            parentFlavor) ]

        # Build trove spec
        troveSpec = [ (x[0], (None, None), (x[1], x[2]), True)
                        for x in binaries ]

        self.cs = repos.createChangeSet(troveSpec, recurse = False)
        self.addLoadedTroves([
            (x.getName(), x.getNewVersion(), x.getNewFlavor()) for x
            in self.cs.iterNewTroveList() ])

        self._expandChangeset()

        AbstractPackageRecipe.unpackSources(self, resume = resume,
                                             downloadOnly = downloadOnly)

    def loadPolicy(self):
        return AbstractPackageRecipe.loadPolicy(self,
                                internalPolicyModules = ( 'derivedpolicy', ) )

    def __init__(self, cfg, laReposCache, srcDirs, extraMacros={},
                 crossCompile=None, lightInstance=False):
        AbstractPackageRecipe.__init__(self, cfg, laReposCache, srcDirs,
                                        extraMacros = extraMacros,
                                        crossCompile = crossCompile,
                                        lightInstance = lightInstance)

        self._addBuildAction('Ant', build.Ant)
        self._addBuildAction('Automake', build.Automake)
        self._addBuildAction('ClassPath', build.ClassPath)
        self._addBuildAction('CompilePython', build.CompilePython)
        self._addBuildAction('Configure', build.Configure)
        self._addBuildAction('ConsoleHelper', build.ConsoleHelper)
        self._addBuildAction('Copy', build.Copy)
        self._addBuildAction('Create', build.Create)
        self._addBuildAction('Desktopfile', build.Desktopfile)
        self._addBuildAction('Doc', build.Doc)
        self._addBuildAction('Environment', build.Environment)
        self._addBuildAction('Install', build.Install)
        self._addBuildAction('JavaCompile', build.JavaCompile)
        self._addBuildAction('JavaDoc', build.JavaDoc)
        self._addBuildAction('Link', build.Link)
        self._addBuildAction('Make', build.Make)
        self._addBuildAction('MakeDirs', build.MakeDirs)
        self._addBuildAction('MakeInstall', build.MakeInstall)
        self._addBuildAction('MakeParallelSubdir', build.MakeParallelSubdir)
        self._addBuildAction('MakePathsInstall', build.MakePathsInstall)
        self._addBuildAction('ManualConfigure', build.ManualConfigure)
        self._addBuildAction('Move', build.Move)
        self._addBuildAction('PythonSetup', build.PythonSetup)
        self._addBuildAction('Remove', build.Remove)
        self._addBuildAction('Replace', build.Replace)
        self._addBuildAction('Run', build.Run)
        self._addBuildAction('SetModes', build.SetModes)
        self._addBuildAction('SGMLCatalogEntry', build.SGMLCatalogEntry)
        self._addBuildAction('Symlink', build.Symlink)
        self._addBuildAction('XInetdService', build.XInetdService)
        self._addBuildAction('XMLCatalogEntry', build.XMLCatalogEntry)

        self._addSourceAction('addArchive', source.addArchive)
        self._addSourceAction('addAction', source.addAction)
        self._addSourceAction('addPatch', source.addPatch)
        self._addSourceAction('addSource', source.addSource)
