#
# The model component of our pseudo-MVC application.
#
import utils
import pages

import derrors, storage

import model_comment

class NoPage:
	type = "bad"
	def exists(self):
		return False
	def displayable(self):
		return False
	def timestamp(self):
		return None
	def modstamp(self):
		return None
	def hashistory(self):
		return False
# We're only ever going to need one of these.
no_page = NoPage()

class User:
	def __init__(self, user, pwhash, groups):
		self.user = user
		self.pwhash = pwhash
		self.groups = groups
		self.username = None
		self.userurl = None

# Return a dict of User objects read from the password file.
# HACK ALERT: password file can include additional information about a
# user, which is written as:
#	.also <user> <user's name> | <user's URL>
def parse_also(line, u):
	s = line.split(None, 2)
	if len(s) != 3:
		raise derrors.AuthErr(".also line has no additional information: "+line)
	s1 = [x.strip() for x in s[2].split("|")]
	if len(s1) != 2:
		raise derrors.AuthErr("bad .also line: "+line)
	if s1[0]:
		u.username = s1[0]
	if s1[1]:
		u.userurl = s1[1]
	
def load_pwfile(cfg):
	if "authfile" not in cfg:
		return {}
	# Okay, try to load it.
	d = {}
	authfile = cfg["authfile"]
	try:
		fp = open(authfile, "r")
		for line in fp:
			line = line.strip()
			if not line or line[0] == '#':
				continue
			nl = line.split(None)
			if len(nl) < 2:
				raise derrors.AuthErr("bad password file line: "+line)
			if nl[0] == '.also':
				if nl[1] not in d:
					raise derrors.AuthErr("user not already known in .also line: "+line)
				parse_also(line, d[nl[1]])
			else:
				u = User(nl[0], nl[1], nl[2:])
				if u.user in d:
					raise derrors.AuthErr("duplicate password file entry for "+u.user)
				d[u.user] = u
		fp.close()
		return d
	except EnvironmentError as e:
		raise derrors.AuthErr("could not read password file %s: %s" % (authfile, str(e)))

# Check that a template is relatively lively.
def validate_template(to, fail_on_error, tname):
	if not fail_on_error:
		if not (to.displayable() and to.type == "file"):
			return None
		else:
			return to
	# Errors:
	if not to.exists():
		raise derrors.IOErr("template '%s' does not exist" % tname)
	if not to.displayable():
		raise derrors.RendErr("template %s is not displayable" % tname)
	if to.type != "file":
		raise derrors.RendErr("template %s is not a file (is a %s)" % (tname, to.type))
	# all okay, go go go.
	return to

#
# Each Model is essentially a database.
# Unfortunately it's a database with a boatload of things glued on to it.
class Model:
	def __init__(self, cfg):
		self.tstore = storage.StoragePool({'dirroot': cfg['tmpldir']})
		dt = {}; dt.update(cfg); dt['dirroot'] = cfg['pagedir']
		if 'usercs' not in cfg:
			self.pstore = storage.StoragePool(dt)
		else:
			self.pstore = storage.RCSStoragePool(dt)
		if 'comments-on' in cfg:
			dt = {}
			dt['dirroot'] = cfg['commentsdir']
			self.cstore = storage.CommentStoragePool(dt)
		else:
			self.cstore = None
		self.pwdict = {}
		self.pcache = {}
		self.cache_on = True
		# We're certainly keeping a lot of copies of the configuration
		# around.
		self.cfg = cfg

	def _fill(self):
		if self.pwdict:
			return
		# The password file is reloaded on every request at the
		# moment, because this avoids staleness the easy way.
		self.pwdict = load_pwfile(self.cfg)

	def finish(self):
		self.pstore.flush()
		self.tstore.flush()
		if self.cstore:
			self.cstore.flush()
		self.pwdict = {}
		self.pcache = {}

	def set_cache(self, state):
		self.cache_on = state
		self.pstore.set_cache(state)

	# Normally, failure to find a usable template by the name
	# in question is a fatal error. However, we can be told
	# not to do so; in that case, failure returns None.
	def get_template(self, tname, fail_on_error = True):
		to = self.tstore.get(tname)
		if not to and fail_on_error:
			raise derrors.IntErr("request for fully bogus template: "+tname)
		elif not to:
			return None
		return validate_template(to, fail_on_error, tname)

	def template_exists(self, tname):
		if not self.tstore.exists(tname):
			return False
		to = self.tstore.get(tname)
		return to and to.type == "file"

	# -------------- page fetching
	# People should not get direct page files unless they know
	# what they're doing. You normally want get_page(), below.
	def get_pfile(self, pagename):
		# If the page doesn't exist, we return no_page instead
		# of making the storage layer go through all the work.
		# This also gives the NoPage stuff a good workout.
		#if not self.pstore.exists(pagename):
		#	return no_page
		
		res = self.pstore.get(pagename, missIsNone = True)
		if not res:
			# Since page names can come from anywhere, we don't
			# allow people to break our rendering by throwing a
			# bad one into somewhere. Instead we return a fake
			# empty page.
			return no_page
		return res

	def get_page(self, pagename):
		if pagename in self.pcache:
			return self.pcache[pagename]
		res = pages.Page(pagename, self)
		if self.cache_on:
			self.pcache[pagename] = res
		return res

	# Virtual pages are *always* inserted into the cache, because
	# subtle detonations lurk in the underbrush if they are not.
	# For the same reason, we manufacture all steps to the virtual
	# page at the same time.
	def get_virtual_page(self, root, suffix):
		fullpath = utils.pjoin(root.path, suffix)
		if fullpath in self.pcache and \
		   isinstance(self.pcache[fullpath], pages.VirtDir):
			return self.pcache[fullpath]
		sl = suffix.split("/")
		for i in range(0, len(sl)):
			fp = utils.pjoin(root.path, '/'.join(sl[:i+1]))
			res = pages.VirtDir(fp, self, root)
			self.pcache[fp] = res
		return self.pcache[fullpath]

	# Get a page for a potentially dubious absolute path. If
	# the path is borked, we return None.
	def get_page_dubious(self, path):
		# fast-path a certain amount of flailing.
		if path in self.pcache:
			return self.pcache[path]
		# No hit, go the full nine yards.
		if not utils.goodpath(path):
			return None
		return self.get_page(path)

	# Canonicalize page names from directory relative to absolute.
	# Chris recants his position that all page names in wiki text
	# should be absolute, because he realizes it doesn't match how
	# people think about filenames in the presence of directories.
	def get_page_relname(self, page, relpath):
		# Already absolute?
		if relpath[0] == '/':
			return self.get_page_dubious(relpath[1:])
		
		# Try to canonicalize the name relative to the current
		# directory. canonpath handles '..', and returns None
		# if the result attempts to back out of the root dir.
		# If that happens, we laugh and reject this thing
		# (we know it is even WORSE as an absolute path!)
		newname = utils.canonpath(page.curdir().path, relpath)
		if newname is None:
			return None

		# If the new name exists, we definetly want it.
		npage = self.get_page_dubious(newname)
		if npage and npage.exists():
			return npage

		# If it doesn't exist as a relative path, we might
		# as well call it an absolute path unless it has
		# crappy bits.
		npage = self.get_page_dubious(relpath)
		if not npage:
			# Crappy bits may be '..', so try the new name.
			return self.get_page_dubious(newname)
		else:
			return npage

	# Walk along a search path, trying to find a page.
	def get_page_paths(self, paths, relpath):
		if not paths or relpath[0] == '/':
			return None
		for p in paths:
			newname = utils.canonpath(p, relpath)
			if newname is None:
				continue
			npage = self.get_page_dubious(newname)
			if npage and npage.exists():
				return npage
		return None

	# Get an alias page, if it exists.
	def get_alias_page(self, pname):
		if "alias-path" not in self.cfg:
			return None
		npath = utils.pjoin(self.cfg["alias-path"], pname)
		res = self.get_page(npath)
		if not res.exists():
			return None
		else:
			return res

	# ------------------ comments
	def comments_on(self):
		return bool(self.cstore)

	# This is an almost-raw interface to fish good things out of
	# the comment store. Note that it will fish both files and
	# directories, and is used for both.
	def _commentpage(self, path):
		res = self.cstore.get(path)
		if not res or not res.exists() or not res.displayable():
			return None
		else:
			return res

	# Return True or False depending on whether the comment posted
	# or not.
	def post_comment(self, comdata, context, username, userurl):
		if not self.cstore or \
		   not context.page.comment_ok(context):
			return False
		nc = model_comment.CommentV1()
		nc.fromform(context, comdata, username, userurl)
		# FIXME: trap errors somehow. For now commentstore
		# failures are truly fatal. (The complication is
		# logging them somehow.)
		try:
			return self.cstore.newblob(context.page.path, str(nc))
		except derrors.WikiErr as e:
			context.set_error("problem posting comment: "+str(e))
			return False

	def get_commentlist(self, page):
		if not self.comments_on():
			return []
		# An undisplayable page turns off its comments.
		if page.type != "file" or not page.displayable():
			return []
		po = self._commentpage(page.path)
		if not po:
			return []
		# Safety check:
		if po.type != "dir":
			raise derrors.IntErr("comment fileobj for '%s' not a directory" % page)
		# This is safe by our axioms; we know that this must be
		# only have files, so we will get a list of comments that
		# the page has.
		return po.contents()

	# This returns a Comment object (qv), not a page object.
	def get_comment(self, page, comment):
		if not self.comments_on():
			return None
		if page.type != 'file' or not page.displayable():
			return None
		compath = utils.pjoin(page.path, comment)
		po = self._commentpage(compath)
		if not po or po.type != "file" or not po.displayable():
			raise derrors.IntErr("missing or undisplayable comment '%s' on page '%s'" % (comment, page))
		c = model_comment.loadcomment(po, comment)
		if c is None:
			raise derrors.IntErr("misformatted comment '%s' on '%s'" % (comment, page.path))
		return c

	# For complicated reasons there is no real point in virtualizing
	# this function through page objects to transparently handle
	# virtual directories. See the comments in _fillcomments() in
	# atomgen.py.
	# It would be possible to virtualize this, but it would make
	# it far less efficient in the common non-virtualized case,
	# because we would have to generate page timestamps for every
	# page with a comment.
	def comments_children(self, page):
		if not self.comments_on():
			return
		for i in self.cstore.children(page.path):
			yield (i[0], utils.parent_path(i[1]),
			       utils.name_path(i[1]))

	# ----------------

	# ---
	# Authentication questions.
	def has_authentication(self):
		return 'authfile' in self.cfg
	
	def get_user(self, user):
		self._fill()
		return self.pwdict.get(user, None)
	# ---

	# Out of a list of views, what is a directory's preferred
	# view? (Note that this makes no sense on files, but you
	# can try it...)
	# This is done with a 'flag' file, named
	#	.flag.prefview:<preferred view>
	# This choice of names insures that it can never be a valid
	# real page, since we block all pages that start with a dot.
	# We cannot use dpage.path straight because then we do the
	# wrong thing on virtualized pages.
	# We inherit things up the path to the root, except for
	# the 'index' view, which is not inherited.
	def pref_view_and_dir(self, dpage, views):
		if dpage.type != "dir":
			return (None, None)
		for cp in utils.walk_to_root(dpage.me()):
			for posview in views:
				if posview == 'index' and cp != dpage:
					continue
				flagn = ".flag.prefview:" + posview
				fpath = utils.pjoin(cp.path, flagn)
				if self.pstore.exists(fpath):
					return (posview, cp)
		return (None, None)

	# Directories can disallow certain views on themselves; such
	# views get redirected to the default view. (Things get slightly
	# weird if they *are* the default view; don't do that.)
	# Of course, we have to check the real page in case of a virtual
	# directory. Like preferred views, this is done with a flag file:
	#	.flag.noview:<disallowed view>
	#
	# TODO: should this (like the preferred view) be inherited down
	# a tree, or should it be purely local as it is now?
	def disallows_view(self, dpage, view):
		dpage = dpage.me()
		if dpage.type != "dir":
			return False
		flagn = ".flag.noview:" + view
		fpath = utils.pjoin(dpage.path, flagn)
		return self.pstore.exists(fpath)

	def is_index_dir(self, dpage):
		if dpage.type != "dir":
			return False
		ip = dpage.child("__index")
		return (ip and ip.displayable())

	# Efficiency considerations rule this particular roost.
	# This returns direct paths, not pages, and you must turn
	# the former into the latter if you want.
	# This is called by page objects, which insure that it is
	# always called with non-virtual pages (and only on directories).
	def _pchildren(self, page):
		return self.pstore.children(page.path)

	# Slightly complicated.
	# Get the template for the page in the given view.
	# This is dwiki/view-<view>-<pagetype>.tmpl or
	# dwiki/view-<view>.tmpl or just dwiki.tmpl, our generic
	# dispatcher.
	def get_view_template(self, page, view):
		for i in ("dwiki/view-%s-%s.tmpl" % (view, page.type),
			  "dwiki/view-%s.tmpl" % view):
			to = self.get_template(i, False)
			if to:
				return to
		return self.get_template("dwiki.tmpl")

	# -----
	#
	def is_util_name(self, name):
		return name.startswith("__")
