#
# Copyright (c) 2004-2005 Specifix, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed in the hope that it will be useful, but
# without any waranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#
import metadata
import os
import sys

import kid
import templates 

from htmlengine import HtmlEngine
from metadata import MDClass
from repository.netrepos import netserver

class ServerError(Exception):
    def __str__(self):
        return self.str
        
class InvalidServerCommand(ServerError):
    str = """Invalid command passed to server."""

class InsufficientPermission(ServerError):
    str = """Insufficient permission for requested operation."""

class HttpHandler(HtmlEngine):
    def __init__(self, repServer):
        self.repServer = repServer
        self.troveStore = repServer.troveStore
        self.templatePath = os.path.dirname(sys.modules['templates'].__file__) + os.path.sep
        
        # "command name": (command handler, page title, 
        #       (requires auth, requires write access, requires admin))
        self.commands = {
             # metadata commands
             "":               (self.mainpage, "Conary Repository",         
                               (True, True, False)),
             "metadata":       (self.metadataCmd, "View Metadata",          
                               (True, True, False)),
             "chooseBranch":   (self.chooseBranchCmd, "View Metadata",
                               (True, True, False)),
             "getMetadata":    (self.getMetadataCmd, "View Metadata",       
                               (True, True, False)),
             "updateMetadata": (self.updateMetadataCmd, "Metadata Updated", 
                               (True, True, False)),
             # user administration commands
             "userlist":       (self.userlistCmd, "User Administration",    
                               (True, True, True)),
             "addUserForm":    (self.addUserFormCmd, "Add User",            
                               (True, True, True)),
             "addUser":        (self.addUserCmd, "Add User",                
                               (True, True, True)),
             # change password commands
             "chPassForm":     (self.chPassFormCmd, "Change Password",
                               (True, False, False)),
             "chPass":         (self.chPassCmd, "Change Password",
                               (True, False, False)),
        }

    def requiresAuth(self, cmd):
        if cmd in self.commands:
            return self.commands[cmd][2][0]
        else:
            return True

    def handleCmd(self, writeFn, cmd, authToken=None, fields=None):
        """Handle either an HTTP POST or GET command."""
        self.setWriter(writeFn)
        if cmd.endswith('/'):
            cmd = cmd[:-1]

        # handle the odd case of style sheet and javascript libraries
        # XXX these items are served with the wrong content-type
        if cmd in ["style.css", "library.js"]:
            f = open(os.path.join(self.templatePath, cmd))
            self.writeFn(f.read())
            f.close()
            return 
    
        if cmd in self.commands:
            handler = self.commands[cmd][0]
            pageTitle = self.commands[cmd][1]
        else:
            raise InvalidServerCommand

        needWrite = self.commands[cmd][2][1]
        needAdmin = self.commands[cmd][2][2]
        if not self.repServer.auth.check(authToken, write=needWrite, admin=needAdmin):
            raise InsufficientPermission

        if cmd == "":
	    home = None
        else:
	    home = self.repServer.urlBase

        self.pageTitle = pageTitle
        handler(authToken, fields)

    def kid_write(self, templateName, **values):
        path = os.path.join(self.templatePath, templateName + ".kid")
        t = kid.load_template(path)
        self.writeFn(t.serialize(encoding="utf-8", pageTitle=self.pageTitle, **values))

    def mainpage(self, authToken, fields):
        self.kid_write("main_page", fields=fields)

    def metadataCmd(self, authToken, fields, troveName=None):
        troveList = [x for x in self.repServer.troveStore.iterTroveNames() if x.endswith(':source')]
        troveList.sort()

        # pick the next trove in the list
        # or stay on the previous trove if canceled
        if "troveName" in fields:
            troveName = fields["troveName"].value
        elif troveName in troveList:
            loc = troveList.index(troveName)
            if loc < len(troveList):
                troveName = troveList[loc+1]

        self.kid_write("pick_trove", troveList = troveList,
                                     troveName = troveName)

    def chooseBranchCmd(self, authToken, fields):
        if fields.has_key('troveName'):
            troveName = fields['troveName'].value
        else:
            troveName = fields['troveNameList'].value
        
        versions = self.repServer.getTroveVersionList(authToken,
            netserver.SERVER_VERSIONS[-1], { troveName : None }, "")
        
        branches = []
        for version in versions[troveName]:
            version = self.repServer.thawVersion(version)
            branches.append(version.branch())

        if len(branches) == 1:
            self._getMetadata(fields, troveName, branches[0].freeze())
        else:
            self.kid_write("choose_branch", branches = branches, troveName = troveName)

    def getMetadataCmd(self, authToken, fields):
        troveName = fields['troveName'].value

        branch = fields['branch'].value
        self._getMetadata(fields, troveName, branch)

    def _getMetadata(self, fields, troveName, branch):
        branch = self.repServer.thawVersion(branch)

        if "source" in fields and fields["source"] == "freshmeat":
            if "freshmeatName" in fields:
                fmName = fields["freshmeatName"].value
            else:
                fmName = troveName[:-7]
            try:
                md = metadata.fetchFreshmeat(fmName)
            except metadata.NoFreshmeatRecord:
                md = None
                self.htmlWarning("No Freshmeat record found.")
        else:
            md = self.troveStore.getMetadata(troveName, branch)

        if not md: # fill a stub
            md = metadata.Metadata(None)

        self.kid_write("metadata", metadata = md, branch = branch,
                                   troveName = troveName)

    def updateMetadataCmd(self, authToken, fields):
        branch = self.repServer.thawVersion(fields["branch"].value)
        troveName = fields["troveName"].value
        
        self.troveStore.updateMetadata(troveName, branch,
            fields["shortDesc"].value,
            fields["longDesc"].value,
            fields.getlist("selUrl"),
            fields.getlist("selLicense"),
            fields.getlist("selCategory"),
            fields["source"].value,
            "C"
        )

        self.metadataCmd(authToken, fields, troveName)
        
    def userlistCmd(self, authToken, fields):
        self.htmlPageTitle("User List")
        userlist = list(self.repServer.auth.iterUsers())
        self.htmlUserlist(userlist)

    def addUserFormCmd(self, authToken, fields):
        self.htmlPageTitle("Add User")
        self.htmlAddUserForm()

    def addUserCmd(self, authToken, fields):
        user = fields["user"].value
        password = fields["password"].value
       
        if fields.has_key("write"):
            write = True
        else:
            write = False

        if fields.has_key("admin"):
            admin = True
        else:
            admin = False
        self.repServer.addUser(authToken, 0, user, password)
        self.repServer.addAcl(authToken, 0, user, "", "", write, True, admin)

        self.writeFn("""User added successfully. <a href="userlist">Return</a>""")
        
    def chPassFormCmd(self, authToken, fields):
        self.htmlPageTitle("Change Password")

        if fields.has_key("username"):
            username = fields["username"].value
            askForOld = False
        else:
            username = authToken[0]
            askForOld = True
            
        self.htmlChPassForm(username, askForOld)
        
    def chPassCmd(self, authToken, fields):
        username = fields["username"].value
        admin = self.repServer.auth.check(authToken, admin=True)
        
        if username != authToken[0]:
            if not admin:
                raise InsufficientPermission
        
        if fields.has_key("oldPassword"):
            oldPassword = fields["oldPassword"].value
        else:
            oldPassword = None
        p1 = fields["password1"].value
        p2 = fields["password2"].value

        self.htmlPageTitle("Change Password")
        if authToken[1] != oldPassword and authToken[0] == username and not admin:
            self.writeFn("""<div class="warning">Error: old password is incorrect</div>""")
        elif p1 != p2:
            self.writeFn("""<div class="warning">Error: passwords do not match</div>""")
        elif oldPassword == p1:
            self.writeFn("""<div class="warning">Error: old and new passwords identical, not changing.</div>""")
        else:
            self.repServer.auth.changePassword(username, p1)
            self.writeFn("""<div>Password successfully changed.</div>""")
