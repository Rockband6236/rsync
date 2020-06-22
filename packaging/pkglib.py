import os, sys, re, subprocess

# This python3 library provides a few helpful routines that are
# used by the latest packaging scripts.

default_encoding = 'utf-8'

# Output the msg args to stderr.  Accepts all the args that print() accepts.
def warn(*msg):
    print(*msg, file=sys.stderr)


# Output the msg args to stderr and die with a non-zero return-code.
# Accepts all the args that print() accepts.
def die(*msg):
    warn(*msg)
    sys.exit(1)


# Set this to an encoding name or set it to None to avoid the default encoding idiom.
def set_default_encoding(enc):
    default_encoding = enc


# Set shell=True if the cmd is a string; sets a default encoding unless raw=True was specified.
def _tweak_opts(cmd, opts, **maybe_set):
    # This sets any maybe_set value that isn't already set AND creates a copy of opts for us.
    opts = {**maybe_set, **opts}

    if type(cmd) == str:
        opts = {'shell': True, **opts}

    want_raw = opts.pop('raw', False)
    if default_encoding and not want_raw:
        opts = {'encoding': default_encoding, **opts}

    capture = opts.pop('capture', None)
    if capture:
        if capture == 'stdout':
            opts = {'stdout': subprocess.PIPE, **opts}
        elif capture == 'stderr':
            opts = {'stderr': subprocess.PIPE, **opts}
        elif capture == 'output':
            opts = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, **opts}
        elif capture == 'combined':
            opts = {'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT, **opts}

    discard = opts.pop('discard', None)
    if discard:
        # We DO want to override any already set stdout|stderr values (unlike above).
        if discard == 'stdout' or discard == 'output':
            opts['stdout'] = subprocess.DEVNULL
        if discard == 'stderr' or discard == 'output':
            opts['stderr'] = subprocess.DEVNULL

    return opts


# This does a normal subprocess.run() with some auto-args added to make life easier.
def cmd_run(cmd, **opts):
    return subprocess.run(cmd, **_tweak_opts(cmd, opts))


# Like cmd_run() with a default check=True specified.
def cmd_chk(cmd, **opts):
    return subprocess.run(cmd, **_tweak_opts(cmd, opts, check=True))


# Capture stdout in a string and return the (output, return_code) tuple.
# Use capture='combined' opt to get both stdout and stderr together.
def cmd_txt_status(cmd, **opts):
    input = opts.pop('input', None)
    if input is not None:
        opts['stdin'] = subprocess.PIPE
    proc = subprocess.Popen(cmd, **_tweak_opts(cmd, opts, capture='stdout'))
    out = proc.communicate(input=input)[0]
    return (out, proc.returncode)


# Like cmd_txt_status() but just return the output.
def cmd_txt(cmd, **opts):
    return cmd_txt_status(cmd, **opts)[0]


# Capture stdout in a string and return the output if the command has a 0 return code.
# Otherwise it throws an exception that indicates the return code and the output.
def cmd_txt_chk(cmd, **opts):
    out, rc = cmd_txt_status(cmd, **opts)
    if rc != 0:
        cmd_err = f'Command "{cmd}" returned non-zero exit status "{rc}" and output:\n{out}'
        raise Exception(cmd_err)
    return out


# Starts a piped-output command of stdout (by default) and leaves it up to you to read
# the output and call communicate() on the returned object.
def cmd_pipe(cmd, **opts):
    return subprocess.Popen(cmd, **_tweak_opts(cmd, opts, capture='stdout'))


# Runs a "git status" command and dies if the checkout is not clean (the
# arg fatal_unless_clean can be used to make that non-fatal.  Returns a
# tuple of the current branch, the is_clean flag, and the status text.
def check_git_status(fatal_unless_clean=True, subdir='.'):
    status_txt = cmd_txt_chk(f"cd '{subdir}' && git status")
    is_clean = re.search(r'\nnothing to commit.+working (directory|tree) clean', status_txt) != None

    if not is_clean and fatal_unless_clean:
        if subdir == '.':
            subdir = ''
        else:
            subdir = f" *{subdir}*"
        die(f"The{subdir} checkout is not clean:\n" + status_txt)

    m = re.match(r'^(?:# )?On branch (.+)\n', status_txt)
    cur_branch = m[1] if m else None

    return (cur_branch, is_clean, status_txt)


# Calls check_git_status() on the current git checkout and (optionally) a subdir path's
# checkout. Use fatal_unless_clean to indicate if an unclean checkout is fatal or not.
# The master_branch arg indicates what branch we want both checkouts to be using, and
# if the branch is wrong the user is given the option of either switching to the right
# branch or aborting.
def check_git_state(master_branch, fatal_unless_clean=True, check_extra_dir=None):
    cur_branch = check_git_status(fatal_unless_clean)[0]
    branch = re.sub(r'^patch/([^/]+)/[^/]+$', r'\1', cur_branch) # change patch/BRANCH/PATCH_NAME into BRANCH
    if branch != master_branch:
        print(f"The checkout is not on the {master_branch} branch.")
        if master_branch != 'master':
            sys.exit(1)
        ans = input(f"Do you want me to continue with --branch={branch}? [n] ")
        if not ans or not re.match(r'^y', ans, flags=re.I):
            sys.exit(1)
        master_branch = branch

    if check_extra_dir and os.path.isdir(os.path.join(check_extra_dir, '.git')):
        branch = check_git_status(fatal_unless_clean, check_extra_dir)[0]
        if branch != master_branch:
            print(f"The *{check_extra_dir}* checkout is on branch {branch}, not branch {master_branch}.")
            ans = input(f"Do you want to change it to branch {master_branch}? [n] ")
            if not ans or not re.match(r'^y', ans, flags=re.I):
                sys.exit(1)
            subdir.check_call(f"cd {check_extra_dir} && git checkout '{master_branch}'", shell=True)

    return (cur_branch, master_branch)


# Return the git hash of the most recent commit.
def latest_git_hash(branch):
    out = cmd_txt_chk(['git', 'log', '-1', '--no-color', branch])
    m = re.search(r'^commit (\S+)', out, flags=re.M)
    if not m:
        die(f"Unable to determine commit hash for master branch: {branch}")
    return m[1]


# Return a set of all branch names that have the format "patch/BASE_BRANCH/NAME"
# for the given base_branch string.  Just the NAME portion is put into the set.
def get_patch_branches(base_branch):
    branches = set()
    proc = cmd_pipe('git branch -l'.split())
    for line in proc.stdout:
        m = re.search(r' patch/([^/]+)/(.+)', line)
        if m and m[1] == base_branch:
            branches.add(m[2])
    proc.communicate()
    return branches


def mandate_gensend_hook():
    hook = '.git/hooks/pre-push'
    if not os.path.exists(hook):
        print('Creating hook file:', hook)
        cmd_chk(['./rsync', '-a', 'packaging/pre-push', hook])
    else:
        out, rc = cmd_txt_status(['fgrep', 'make gensend', hook], discard='output')
        if rc:
            die('Please add a "make gensend" into your', hook, 'script.')


# Snag the GENFILES values out of the Makefile.in file and return them as a list.
def get_gen_files():
    cont_re = re.compile(r'\\\n')

    extras = [ ]

    with open('Makefile.in', 'r', encoding='utf-8') as fh:
        for line in fh:
            if not extras:
                chk = re.sub(r'^GENFILES=', '', line)
                if line == chk:
                    continue
                line = chk
            m = re.search(r'\\$', line)
            line = re.sub(r'^\s+|\s*\\\n?$|\s+$', '', line)
            extras += line.split()
            if not m:
                break

    return extras


def get_configure_version():
    with open('configure.ac', 'r', encoding='utf-8') as fh:
        for line in fh:
            m = re.match(r'^AC_INIT\(\[rsync\],\s*\[(\d.+?)\]', line)
            if m:
                return m[1]
    die("Unable to find AC_INIT with version in configure.ac")


def get_NEWS_version_info():
    rel_re = re.compile(r'^\| \d{2} \w{3} \d{4}\s+\|\s+(?P<ver>\d+\.\d+\.\d+)\s+\|\s+(?P<pdate>\d{2} \w{3} \d{4}\s+)?\|\s+(?P<pver>\d+)\s+\|')
    last_version = last_protocol_version = None
    pdate = { }

    with open('NEWS.md', 'r', encoding='utf-8') as fh:
        for line in fh:
            if not last_version:
                m = re.search(r'rsync (\d+\.\d+\.\d+).*\d\d\d\d', line)
                if m:
                    last_version = m[1]
            m = rel_re.match(line)
            if m:
                if m['pdate']:
                    pdate[m['ver']] = m['pdate']
                if m['ver'] == last_version:
                    last_protocol_version = m['pver']
                    break

    if not last_protocol_version:
        die(f"Unable to determine protocol_version for {last_version}.")

    return last_version, last_protocol_version


def get_protocol_versions():
    protocol_version = subprotocol_version = None

    with open('rsync.h', 'r', encoding='utf-8') as fh:
        for line in fh:
            m = re.match(r'^#define\s+PROTOCOL_VERSION\s+(\d+)', line)
            if m:
                protocol_version = m[1]
                continue
            m = re.match(r'^#define\s+SUBPROTOCOL_VERSION\s+(\d+)', line)
            if m:
                subprotocol_version = m[1]
                break

    if not protocol_version:
        die("Unable to determine the current PROTOCOL_VERSION.")

    if not subprotocol_version:
        die("Unable to determine the current SUBPROTOCOL_VERSION.")

    return protocol_version, subprotocol_version

# vim: sw=4 et
