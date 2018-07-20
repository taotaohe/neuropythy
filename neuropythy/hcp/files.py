####################################################################################################
# neuropythy/hcp/files.py
# Stored data regarding the organization of the files in HCP subjects.
# by Noah C. Benson

import os, six, pimms, pyrsistent as pyr, nibabel as nib, numpy as np
import neuropythy.io as nyio
from neuropythy.util import is_image

# this isn't required, but if we can load it we will use it for auto-downloading subject data
try:    import s3fs
except: s3fs = None

####################################################################################################
# Subject Directory and where to find Subjects

_subjects_dirs = pyr.v()

def subject_paths():
    '''
    subject_paths() yields a list of paths to HCP subject directories in which subjects are
      automatically searched for when identified by subject-name only. These paths are searched in
      the order returned from this function.

    If you must edit these paths, it is recommended to use add_subject_path, and clear_subject_paths
    functions.
    '''
    return _subjects_dirs
def clear_subject_paths(subpaths):
    '''
    clear_subject_paths() resets the HCP subject paths to be empty and yields the previous
      list of subject paths.
    '''
    sd = _subjects_dirs
    _subjects_dirs = pyr.v()
    return sd
def add_subject_path(path, index=None):
    '''
    add_subject_path(path) will add the given path to the list of subject directories in which to
      search for HCP subjects. The optional argument index may be given to specify the precedence of
      this path when searching for a new subject; the default, 0, always inserts the path at the
      front of the list; a value of k indicates that the new list should have the new path at index
      k.
    The path may contain :'s, in which case the individual directories are separated and added.  If
    the given path is not a directory or the path could not be inserted, yields False; otherwise,
    yields True. If the string contains a : and multiple paths, then True is yielded only if all
    paths were successfully inserted.  See also subject_paths.
    '''
    global _subjects_dirs
    paths = [p for p in path.split(':') if len(p) > 0]
    if len(paths) > 1:
        tests = [add_subject_path(p, index=index) for p in reversed(paths)]
        return all(t for t in tests)
    else:
        path = os.path.expanduser(path)
        if not os.path.isdir(path): return False
        if path in _subjects_dirs:  return True
        try:
            if index is None or index is Ellipsis:
                _subjects_dirs = _subjects_dirs.append(path)
            else:
                sd = _subjects_dirs.tolist()
                sd.insert(index, path)
                _subjects_dirs = pyr.pvector(sd)
            return True
        except:
            return False
def find_subject_path(sid):
    '''
    find_subject_path(sub) yields the full path of a HCP subject with the name given by the string
      sub, if such a subject can be found in the HCP search paths. See also add_subject_path.

    If no subject is found, then None is returned.
    '''
    # if it's a full/relative path already, use it:
    sub = str(sid)
    if os.path.isdir(sub): return os.path.abspath(sub)
    pth = next((os.path.abspath(p)
                for sd in _subjects_dirs for p in [os.path.join(sd, sub)]
                if os.path.isdir(p)),
               None)
    if pth is not None: return pth
    # see if we can create them
    if _auto_download_options is None or not _auto_downloadable(sid): return None
    pth = os.path.join(_auto_download_options['subjects_path'], sub)
    if os.path.isdir(pth): return pth
    try: os.makedirs(pth, 0755)
    except: return None
    return pth

# add the SUBJECTS_DIR environment variable...
for varname in ['HCP_SUBJECTS_DIR', 'HCPSUBJS_DIR']:
    if varname in os.environ:
        add_subject_path(os.environ[varname])
for varname in ['HCP_ROOT', 'HCP_DIR']:
    if varname in os.environ:
        dirname = os.path.join(os.environ[varname], 'subjects')
        if os.path.isdir(dirname):
            add_subject_path(dirname)

####################################################################################################
# Utilities

def to_subject_id(s):
    '''
    to_subject_id(s) coerces the given string or number into an integer subject id. If s is not a
      valid subejct id, raises an exception.
    '''
    if not pimms.is_number(s) and not pimms.is_str(s):
        raise ValueError('invalid type for subject id: %s' % str(type(s)))
    if pimms.is_str(s):
        try: s = os.path.expanduser(s)
        except: pass
        if os.path.isdir(s): s = s.split(os.sep)[-1]
    s = int(s)
    if s > 999999 or s < 100000:
        raise ValueError('subject ids must be 6-digit integers whose first digit is > 0')
    return s
def load_credentials(flnm):
    '''
    load_credentials(filename) loads the HCP Amazon Bucket credentials stored in the given file. The
      file must contain <key>:<secret> on a single line. If the file does not contain valid
      credentials, then an exception is raised. Yields (key, secret).
    '''
    with open(flnm, 'r') as fl:
        dat = fl.read(1024 * 8)
    dat = dat.strip().split(':')
    if len(dat) != 2: raise ValueError('File %s does not appear to be a credentials file' % flnm)
    return tuple(dat)
def to_credentials(arg):
    '''
    to_credentials(arg) converts arg into a pair (key, secret) if arg can be coerced into such a
      pair and otherwise raises an error.
    
    Possible inputs include:
      * A tuple (key, secret)
      * A mapping with the keys 'key' and 'secret'
      * The name of a file that can load credentials via the load_credentials() function
    '''
    if pimms.is_str(arg):
        try:    return load_credentials(arg)
        except: return tuple(arg.strip().split(':'))
    elif pimms.is_map(arg) and 'key' in arg and 'secret' in arg: return (arg['key'], arg['secret'])
    elif pimms.is_vector(arg) and len(arg) == 2 and all(pimms.is_str(x) for x in arg):
        return tuple(arg)
    else:
        raise ValueError('given argument cannot be coerced to credentials')
def detect_credentials():
    '''
    detect_credentials() attempts to locate Amazon AWS Bucket credentials from a number of sources:
      - first, if the environment contains the variable HCP_CREDENTIALS, containing a string with
        the format "<key>:<secret>" this is used;
      - next, if the environment contains the variables HCP_KEY and HCP_SECRET, these are used;
      - next, if the files ~/.hcp-credentials or ~/.hcp-passwd or ~/.passwd-hcp are found, their
        contents are used, in that order.
      - next, all of the above are rechecked with the strings HCP/hcp replaced with S3FS/s3fs;
      - finally, if no credentials were detected, an error is raised.
    '''
    for tag in ['hcp', 's3fs']:
        utag = tag.upper()
        if (utag + '_CREDENTIALS') in os.environ:
            return to_credentials(os.environ[utag + '_CREDENTIALS'])
        if all((utag + s) in os.environ for s in ['_KEY', '_SECRET']):
            return (os.environ[utag + '_KEY'], os.environ[utag + '_SECRET'])
        for pth in ['%s-credentials', '%s-passwd', 'passwd-%s']:
            pth = os.path.expanduser('~/.' + (pth % tag))
            if os.path.isfile(pth):
                try: return load_credentials(pth)
                except: pass
    # no match!
    raise ValueError('No valid credentials for the HCP were detected')

####################################################################################################
# Subject Data Structure
# This structure details how neuropythy understands an HCP subject to be structured.

# A few loading functions used by the description below
# Used to load immutable-like mgh objects
def _data_load(filename, data):
    sid = data['id']
    # First, see if the file exists, and whether we can auto-download it
    if not os.path.isfile(filename):
        if _auto_download_options is None or not _auto_downloadable(sid):
            raise ValueError('File %s not found' % filename)
        fs = _auto_download_options['s3fs']
        db = _auto_download_options['database']
        rl = _auto_download_options['release']
        # parse the path apart by subject directory
        relpath = filename.split(str(sid) + os.sep)[-1]
        hcp_sdir = '/'.join([db, rl, str(sid)])
        if not fs.exists(hcp_sdir):
            raise ValueError('Subject %d not found in release' % sid)
        hcp_flnm = '/'.join([hcp_sdir, relpath])
        # download it...
        basedir = os.path.split(filename)[0]
        if not os.path.isdir(basedir): os.makedirs(basedir, 0755)
        #print 'Downloading file %s ...' % filename
        fs.get(hcp_flnm, filename)
    # If the data says it's a cifti...
    if 'cifti' in data and data['cifti']:
        res = nib.load(filename).get_data()
        res = np.squeeze(res)
    elif data['type'] in ('surface', 'registration'):
        res = nyio.load(filename)
    elif data['type'] == 'property':
        if filename.endswith('.gii') or filename.endswith('.gii.gz'):
            res = nyio.load(filename).darrays[0].data
        else:
            res = nyio.load(filename)
        res = np.squeeze(res)
    elif data['type'] == 'image':
        res = nyio.load(filename)
    else:
        raise ValueError('unrecognized data type: %s' % data['type'])
    return res
def _load(filename, data):
    if 'load' in data and data['load'] is not None:
        res = data['load'](filename, data)
    else:
        res = _data_load(filename, data)
    # do the filter if there is one
    if 'filt' in data and data['filt'] is not None:
        res = data['filt'](res)
    # persist and return
    if is_image(res):
        res.get_data().setflags(write=False)
    elif pimms.is_imm(res):
        res.persist()
    elif pimms.is_nparray(res):
        res.setflags(write=False)
    return res
def _load_atlas_sphere(filename, data):
    atlases = _load_atlas_sphere.atlases
    (fdir, fnm) = os.path.split(filename)
    (sid, h, x1, atlas, x2, x3) = fnm.split('.')
    sid = int(sid)
    h = h.lower() + 'h'
    if x2 != 'surf':
        raise ValueError('bad filename for atlas sphere: %s' % filename)
    cache = atlases[h]
    addr = atlas + '.' + x1
    if addr not in cache:
        cache[addr] = _load(filename, pimms.merge(data, {'load':None}))
    return cache[addr]
_load_atlas_sphere.atlases = {'lh':{}, 'rh':{}}
def _load_fsLR_atlasroi(filename, data):
    '''
    Loads the appropriate atlas for the given data; data may point to a cifti file whose atlas is
    needed or to an atlas file.
    '''
    (fdir, fnm) = os.path.split(filename)
    fparts = fnm.split('.')
    atl = fparts[-3]
    if atl in _load_fsLR_atlasroi.atlases: return _load_fsLR_atlasroi.atlases[atl]
    sid = data['id']
    fnm = [os.path.join(fdir, '%d.%s.atlasroi.%s.shape.gii' % (sid, h, atl))  for h in ('L', 'R')]
    if data['cifti']:
        dat = [{'id':data['id'], 'type':'property', 'name':'atlas', 'hemi':h} for h in data['hemi']]
    else:
        dat = [{'id':data['id'], 'type':'property', 'name':'atlas', 'hemi':(h + data['hemi'][2:])}
               for h in ('lh','rh')]
    # loading an atlas file; this is easier
    rois = tuple([_load(f, d) for (f,d) in zip(fnm, dat)])
    # add these to the cache
    if atl != 'native': _load_fsLR_atlasroi.atlases[atl] = rois
    return rois
_load_fsLR_atlasroi.atlases = {}

# The description of the entire subject directory that we care about:
subject_directory_structure = {
    'T1w': {'type':'dir',
            'contents': {
                'BiasField_acpc_dc.nii.gz':         {'type':'image', 'name':'bias'},
                'T1wDividedByT2w.nii.gz':           {'type':'image', 'name':'T1_to_T2_ratio_all'},
                'T1wDividedByT2w_ribbon.nii.gz':    {'type':'image', 'name':'T1_to_T2_ratio'},
                'T1w_acpc_dc_restore.nii.gz':       {'type':'image', 'name':'T1'},
                'T1w_acpc_dc.nii.gz':               {'type':'image', 'name':'T1_unrestored'},
                'T1w_acpc_dc_restore_brain.nii.gz': {'type':'image', 'name':'brain'},
                'T2w_acpc_dc_restore.nii.gz':       {'type':'image', 'name':'T2'},
                'T2w_acpc_dc.nii.gz':               {'type':'image', 'name':'T2_unrestored'},
                'T2w_acpc_dc_restore_brain.nii.gz': {'type':'image', 'name':'T2_brain'},
                'aparc+aseg.nii.gz':                {'type':'image', 'name':'parcellation2005'},
                'aparc.a2009s+aseg.nii.gz':         {'type':'image', 'name':'parcellation'},
                'brainmask_fs.nii.gz':              {'type':'image', 'name':'brainmask'},
                'ribbon.nii.gz':                    {'type':'image', 'name':'ribbon'},
                'wmparc.nii.gz':                    {'type':'image', 'name':'wm_parcellation'},
                'Native': {
                    'type':'dir',
                    'contents': {
                        '{0[id]}.L.white.native.surf.gii': (
                            {'type':'surface',
                             'name':'white',
                             'hemi':'lh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'white',
                             'hemi':'lh_native_MSMAll'}),
                        '{0[id]}.L.midthickness.native.surf.gii': (
                            {'type':'surface',
                             'name':'midgray',
                             'hemi':'lh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'midgray',
                             'hemi':'lh_native_MSMAll'}),
                        '{0[id]}.L.pial.native.surf.gii':(
                            {'type':'surface',
                             'name':'pial',
                             'hemi':'lh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'pial',
                             'hemi':'lh_native_MSMAll'}),
                        '{0[id]}.L.inflated.native.surf.gii': (
                            {'type':'surface',
                             'name':'inflated',
                             'hemi':'lh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'inflated',
                             'hemi':'lh_native_MSMAll'}),
                        '{0[id]}.L.very_inflated.native.surf.gii': (
                            {'type':'surface',
                             'name':'very_inflated',
                             'hemi':'lh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'very_inflated',
                             'hemi':'lh_native_MSMAll'}),
                        '{0[id]}.R.white.native.surf.gii': (
                            {'type':'surface',
                             'name':'white',
                             'hemi':'rh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'white',
                             'hemi':'rh_native_MSMAll'}),
                        '{0[id]}.R.midthickness.native.surf.gii': (
                            {'type':'surface',
                             'name':'midgray',
                             'hemi':'rh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'midgray',
                             'hemi':'rh_native_MSMAll'}),
                        '{0[id]}.R.pial.native.surf.gii':(
                            {'type':'surface',
                             'name':'pial',
                             'hemi':'rh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'pial',
                             'hemi':'rh_native_MSMAll'}),
                        '{0[id]}.R.inflated.native.surf.gii': (
                            {'type':'surface',
                             'name':'inflated',
                             'hemi':'rh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'inflated',
                             'hemi':'rh_native_MSMAll'}),
                        '{0[id]}.R.very_inflated.native.surf.gii': (
                            {'type':'surface',
                             'name':'very_inflated',
                             'hemi':'rh_native_MSMSulc'},
                            {'type':'surface',
                             'name':'very_inflated',
                             'hemi':'rh_native_MSMAll'})}},
                'fsaverage_LR32k': {
                    'type':'dir',
                    'contents': {
                        '{0[id]}.L.inflated.32k_fs_LR.surf.gii':      {
                            'type':'surface',
                            'name':'inflated',
                            'hemi':'lh_lowres_MSMSulc'},
                        '{0[id]}.L.midthickness.32k_fs_LR.surf.gii':  {
                            'type':'surface',
                            'name':'midgray',
                            'hemi':'lh_lowres_MSMSulc'},
                        '{0[id]}.L.pial.32k_fs_LR.surf.gii':          {
                            'type':'surface',
                            'name':'pial',
                            'hemi':'lh_lowres_MSMSulc'},
                        '{0[id]}.L.very_inflated.32k_fs_LR.surf.gii': {
                            'type':'surface',
                            'name':'very_inflated',
                            'hemi':'lh_lowres_MSMSulc'},
                        '{0[id]}.L.white.32k_fs_LR.surf.gii':         {
                            'type':'surface',
                            'name':'white',
                            'hemi':'lh_lowres_MSMSulc'},
                        '{0[id]}.R.inflated.32k_fs_LR.surf.gii':      {
                            'type':'surface',
                            'name':'inflated',
                            'hemi':'rh_lowres_MSMSulc'},
                        '{0[id]}.R.midthickness.32k_fs_LR.surf.gii':  {
                            'type':'surface',
                            'name':'midgray',
                            'hemi':'rh_lowres_MSMSulc'},
                        '{0[id]}.R.pial.32k_fs_LR.surf.gii':          {
                            'type':'surface',
                            'name':'pial',
                            'hemi':'rh_lowres_MSMSulc'},
                        '{0[id]}.R.very_inflated.32k_fs_LR.surf.gii': {
                            'type':'surface',
                            'name':'very_inflated',
                            'hemi':'rh_lowres_MSMSulc'},
                        '{0[id]}.R.white.32k_fs_LR.surf.gii':         {
                            'type':'surface',
                            'name':'white',
                            'hemi':'rh_lowres_MSMSulc'},
                        '{0[id]}.L.inflated_MSMAll.32k_fs_LR.surf.gii':      {
                            'type':'surface',
                            'name':'inflated',
                            'hemi':'lh_lowres_MSMAll'},
                        '{0[id]}.L.midthickness_MSMAll.32k_fs_LR.surf.gii':  {
                            'type':'surface',
                            'name':'midgray',
                            'hemi':'lh_lowres_MSMAll'},
                        '{0[id]}.L.pial_MSMAll.32k_fs_LR.surf.gii':          {
                            'type':'surface',
                            'name':'pial',
                            'hemi':'lh_lowres_MSMAll'},
                        '{0[id]}.L.very_inflated_MSMAll.32k_fs_LR.surf.gii': {
                            'type':'surface',
                            'name':'very_inflated',
                            'hemi':'lh_lowres_MSMAll'},
                        '{0[id]}.L.white_MSMAll.32k_fs_LR.surf.gii':         {
                            'type':'surface',
                            'name':'white',
                            'hemi':'lh_lowres_MSMAll'},
                        '{0[id]}.R.inflated_MSMAll.32k_fs_LR.surf.gii':      {
                            'type':'surface',
                            'name':'inflated',
                            'hemi':'rh_lowres_MSMAll'},
                        '{0[id]}.R.midthickness_MSMAll.32k_fs_LR.surf.gii':  {
                            'type':'surface',
                            'name':'midgray',
                            'hemi':'rh_lowres_MSMAll'},
                        '{0[id]}.R.pial_MSMAll.32k_fs_LR.surf.gii':          {
                            'type':'surface',
                            'name':'pial',
                            'hemi':'rh_lowres_MSMAll'},
                        '{0[id]}.R.very_inflated_MSMAll.32k_fs_LR.surf.gii': {
                            'type':'surface',
                            'name':'very_inflated',
                            'hemi':'rh_lowres_MSMAll'},
                        '{0[id]}.R.white_MSMAll.32k_fs_LR.surf.gii':         {
                            'type':'surface',
                            'name':'white',
                            'hemi':'rh_lowres_MSMAll'}}}}},
    'MNINonLinear': {
        'type': 'dir',
        'contents': {
            'BiasField.nii.gz':         {'type':'image', 'name':'bias_warped'},
            'T1w_restore.nii.gz':       {'type':'image', 'name':'T1_warped'},
            'T1w.nii.gz':               {'type':'image', 'name':'T1_warped_unrestored'},
            'T1w_restore_brain.nii.gz': {'type':'image', 'name':'brain_warped'},
            'T2w_restore.nii.gz':       {'type':'image', 'name':'T2_warped'},
            'T2w.nii.gz':               {'type':'image', 'name':'T2_warped_unrestored'},
            'T2w_restore_brain.nii.gz': {'type':'image', 'name':'T2_brain_warped'},
            'aparc+aseg.nii.gz':        {'type':'image', 'name':'parcellation2005_warped'},
            'aparc.a2009s+aseg.nii.gz': {'type':'image', 'name':'parcellation_warped'},
            'brainmask_fs.nii.gz':      {'type':'image', 'name':'brainmask_warped'},
            'ribbon.nii.gz':            {'type':'image', 'name':'ribbon_warped'},
            'wmparc.nii.gz':            {'type':'image', 'name':'wm_parcellation_warped'},
            '{0[id]}.L.ArealDistortion_FS.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'areal_distortion_FS',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.ArealDistortion_MSMSulc.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'areal_distortion',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.MyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.MyelinMap_BC.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_bc',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.SmoothedMyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_smooth',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.SmoothedMyelinMap_BC.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_smooth_bc',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.RefMyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_ref',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.BA.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'brodmann_area',
                 'hemi':'lh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'brodmann_area',
                 'hemi':'lh_LR164k_MSMAll'}),
            '{0[id]}.L.aparc.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'parcellation_2005',
                 'hemi':'lh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'parcellation_2005',
                 'hemi':'lh_LR164k_MSMAll'}),
            '{0[id]}.L.aparc.a2009s.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'parcellation',
                 'hemi':'lh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'parcellation',
                 'hemi':'lh_LR164k_MSMAll'}),
            '{0[id]}.L.atlasroi.164k_fs_LR.shape.gii': (
                {'type':'property',
                 'name':'atlas',
                 'hemi':'lh_LR164k_MSMSulc',
                 'load':_load_fsLR_atlasroi,
                 'filt':lambda x:x[0].astype(np.bool)},
                {'type':'property',
                 'name':'atlas',
                 'hemi':'lh_LR164k_MSMAll',
                 'load':_load_fsLR_atlasroi,
                 'filt':lambda x:x[0].astype(np.bool)}),
            '{0[id]}.L.curvature.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'curvature',
                'hemi':'lh_LR164k_MSMSulc',
                'filt':lambda c: -c},
            '{0[id]}.L.sulc.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'convexity',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.corrThickness.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'thickness',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.thickness.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'thickness_uncorrected',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.white.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'white',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.midthickness.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'midgray',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.pial.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'pial',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.inflated.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'inflated',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.very_inflated.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'very_inflated',
                'hemi':'lh_LR164k_MSMSulc'},
            '{0[id]}.L.white_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'white',
                'hemi':'lh_LR164k_MSMAll'},
            '{0[id]}.L.midthickness_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'midgray',
                'hemi':'lh_LR164k_MSMAll'},
            '{0[id]}.L.pial_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'pial',
                'hemi':'lh_LR164k_MSMAll'},
            '{0[id]}.L.inflated_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'inflated',
                'hemi':'lh_LR164k_MSMAll'},
            '{0[id]}.L.very_inflated_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'very_inflated',
                'hemi':'lh_LR164k_MSMAll'},
            '{0[id]}.L.sphere.164k_fs_LR.surf.gii': (
                {'type':'registration',
                 'name':'fs_LR',
                 'hemi':'lh_LR164k_MSMSulc',
                 'load':_load_atlas_sphere},
                {'type':'registration',
                 'name':'fs_LR',
                 'hemi':'lh_LR164k_MSMAll',
                 'load':_load_atlas_sphere}),
            '{0[id]}.L.flat.164k_fs_LR.surf.gii': (
                {'type':'surface',
                 'name':'flat',
                 'hemi':'lh_LR164k_MSMSulc',
                 'load':_load_atlas_sphere},
                {'type':'surface',
                 'name':'flat',
                 'hemi':'lh_LR164k_MSMAll',
                 'load':_load_atlas_sphere}),
            '{0[id]}.R.ArealDistortion_FS.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'areal_distortion_FS',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.ArealDistortion_MSMSulc.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'areal_distortion',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.MyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.MyelinMap_BC.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_bc',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.SmoothedMyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_smooth',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.SmoothedMyelinMap_BC.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_smooth_bc',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.RefMyelinMap.164k_fs_LR.func.gii': {
                'type':'property',
                'name':'myelin_ref',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.BA.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'brodmann_area',
                 'hemi':'rh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'brodmann_area',
                 'hemi':'rh_LR164k_MSMAll'}),
            '{0[id]}.R.aparc.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'parcellation_2005',
                 'hemi':'rh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'parcellation_2005',
                 'hemi':'rh_LR164k_MSMAll'}),
            '{0[id]}.R.aparc.a2009s.164k_fs_LR.label.gii': (
                {'type':'property',
                 'name':'parcellation',
                 'hemi':'rh_LR164k_MSMSulc'},
                {'type':'property',
                 'name':'parcellation',
                 'hemi':'rh_LR164k_MSMAll'}),
            '{0[id]}.R.atlasroi.164k_fs_LR.shape.gii': (
                {'type':'property',
                 'name':'atlas',
                 'hemi':'rh_LR164k_MSMSulc',
                 'load':_load_fsLR_atlasroi,
                 'filt':lambda x:x[1].astype(np.bool)},
                {'type':'property',
                 'name':'atlas',
                 'hemi':'rh_LR164k_MSMAll',
                 'load':_load_fsLR_atlasroi,
                 'filt':lambda x:x[1].astype(np.bool)}),
            '{0[id]}.R.curvature.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'curvature',
                'hemi':'rh_LR164k_MSMSulc',
                'filt':lambda c: -c},
            '{0[id]}.R.sulc.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'convexity',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.corrThickness.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'thickness',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.thickness.164k_fs_LR.shape.gii': {
                'type':'property',
                'name':'thickness_uncorrected',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.sulc.164k_fs_LR.shape.gii': {
                'type':'surface',
                'name':'convexity',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.white.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'white',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.midthickness.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'midgray',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.pial.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'pial',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.inflated.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'inflated',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.very_inflated.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'very_inflated',
                'hemi':'rh_LR164k_MSMSulc'},
            '{0[id]}.R.white_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'white',
                'hemi':'rh_LR164k_MSMAll'},
            '{0[id]}.R.midthickness_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'midgray',
                'hemi':'rh_LR164k_MSMAll'},
            '{0[id]}.R.pial_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'pial',
                'hemi':'rh_LR164k_MSMAll'},
            '{0[id]}.R.inflated_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'inflated',
                'hemi':'rh_LR164k_MSMAll'},
            '{0[id]}.R.very_inflated_MSMAll.164k_fs_LR.surf.gii': {
                'type':'surface',
                'name':'very_inflated',
                'hemi':'rh_LR164k_MSMAll'},
            '{0[id]}.R.sphere.164k_fs_LR.surf.gii': (
                {'type':'registration',
                 'name':'fs_LR',
                 'hemi':'rh_LR164k_MSMSulc',
                 'load':_load_atlas_sphere},
                {'type':'registration',
                 'name':'fs_LR',
                 'hemi':'rh_LR164k_MSMAll',
                 'load':_load_atlas_sphere}),
            '{0[id]}.R.flat.164k_fs_LR.surf.gii': (
                {'type':'surface',
                 'name':'flat',
                 'hemi':'rh_LR164k_MSMSulc',
                 'load':_load_atlas_sphere},
                {'type':'surface',
                 'name':'flat',
                 'hemi':'rh_LR164k_MSMAll',
                 'load':_load_atlas_sphere}),
            '{0[id]}.ArealDistortion_MSMAll.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'areal_distortion',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            '{0[id]}.MyelinMap_BC_MSMAll.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'myelin_bc',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            '{0[id]}.SmoothedMyelinMap_BC_MSMAll.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'myelin_smooth_bc',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            '{0[id]}.curvature_MSMAll.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'curvature',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll'),
                'filt':lambda c: -c},
            '{0[id]}.sulc.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'convexity',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            '{0[id]}.corrThickness.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'thickness',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            '{0[id]}.thickness.164k_fs_LR.dscalar.nii': {
                'type':'property',
                'name':'thickness_uncorrected',
                'hemi':('lh_LR164k_MSMAll', 'rh_LR164k_MSMAll')},
            'Native': {
                'type':'dir',
                'contents': {
                    '{0[id]}.L.ArealDistortion_FS.native.shape.gii': (
                        {'type':'property',
                         'name':'areal_distortion_FS',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'areal_distortion_FS',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.ArealDistortion_MSMSulc.native.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.ArealDistortion_MSMAll.native.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'lh_native_MSMAll'},
                    '{0[id]}.L.MyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.MyelinMap_BC.native.func.gii': {
                        'type':'property',
                        'name':'myelin_bc',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.SmoothedMyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.SmoothedMyelinMap_BC.native.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth_bc',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.RefMyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin_ref',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.L.BA.native.label.gii': (
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.aparc.native.label.gii':  (
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.aparc.a2009s.native.label.gii': (
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.atlasroi.native.shape.gii': (
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.curvature.native.shape.gii': (
                        {'type':'property',
                         'name':'curvature',
                         'hemi':'lh_native_MSMSulc',
                         'filt':lambda c: -c},
                        {'type':'property',
                         'name':'curvature',
                         'hemi':'lh_native_MSMAll',
                         'filt':lambda c: -c}),
                    '{0[id]}.L.sulc.native.shape.gii': (
                        {'type':'property',
                         'name':'convexity',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'convexity',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.corrThickness.native.shape.gii': (
                        {'type':'property',
                         'name':'thickness',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'thickness',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.thickness.native.shape.gii': (
                        {'type':'property',
                         'name':'thickness_uncorrected',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'property',
                         'name':'thickness_uncorrected',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.roi.native.shape.gii': (
                        {'type':'property',
                         'name':'roi',
                         'hemi':'lh_native_MSMSulc',
                         'filt':lambda r: r.astype(bool)},
                        {'type':'property',
                         'name':'roi',
                         'hemi':'lh_native_MSMAll',
                         'filt':lambda r: r.astype(bool)}),
                    '{0[id]}.L.sphere.native.surf.gii': (
                        {'type':'registration',
                         'name':'native',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'registration',
                         'name':'native',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.sphere.reg.native.surf.gii': (
                        {'type':'registration',
                         'name':'fsaverage',
                         'hemi':'lh_native_MSMSulc'},
                        {'type':'registration',
                         'name':'fsaverge',
                         'hemi':'lh_native_MSMAll'}),
                    '{0[id]}.L.sphere.MSMAll.native.surf.gii': {
                        'type':'registration',
                        'name':'fs_LR',
                        'tool':'MSMAll',
                        'hemi':'lh_native_MSMAll'},
                    '{0[id]}.L.sphere.MSMSulc.native.surf.gii': {
                        'type':'registration',
                        'name':'fs_LR',
                        'tool':'MSMSulc',
                        'hemi':'lh_native_MSMSulc'},
                    '{0[id]}.R.ArealDistortion_FS.native.shape.gii': (
                        {'type':'property',
                         'name':'areal_distortion_FS',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'areal_distortion_FS',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.ArealDistortion_MSMSulc.native.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.ArealDistortion_MSMAll.native.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'rh_native_MSMAll'},
                    '{0[id]}.R.MyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.MyelinMap_BC.native.func.gii': {
                        'type':'property',
                        'name':'myelin_bc',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.SmoothedMyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.SmoothedMyelinMap_BC.native.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth_bc',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.RefMyelinMap.native.func.gii': {
                        'type':'property',
                        'name':'myelin_ref',
                        'hemi':'rh_native_MSMSulc'},
                    '{0[id]}.R.BA.native.label.gii': (
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.aparc.native.label.gii':  (
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.aparc.a2009s.native.label.gii': (
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.atlasroi.native.shape.gii': (
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.curvature.native.shape.gii': (
                        {'type':'property',
                         'name':'curvature',
                         'hemi':'rh_native_MSMSulc',
                        'filt':lambda c: -c},
                        {'type':'property',
                         'name':'curvature',
                         'hemi':'rh_native_MSMAll',
                         'filt':lambda c: -c}),
                    '{0[id]}.R.sulc.native.shape.gii': (
                        {'type':'property',
                         'name':'convexity',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'convexity',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.corrThickness.native.shape.gii': (
                        {'type':'property',
                         'name':'thickness',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'thickness',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.thickness.native.shape.gii': (
                        {'type':'property',
                         'name':'thickness_uncorrected',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'property',
                         'name':'thickness_uncorrected',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.roi.native.shape.gii': (
                        {'type':'property',
                         'name':'roi',
                         'hemi':'rh_native_MSMSulc',
                         'filt':lambda r: r.astype(bool)},
                        {'type':'property',
                         'name':'roi',
                         'hemi':'rh_native_MSMAll',
                         'filt':lambda r: r.astype(bool)}),
                    '{0[id]}.R.sphere.native.surf.gii': (
                        {'type':'registration',
                         'name':'native',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'registration',
                         'name':'native',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.sphere.reg.native.surf.gii': (
                        {'type':'registration',
                         'name':'fsaverage',
                         'hemi':'rh_native_MSMSulc'},
                        {'type':'registration',
                         'name':'fsaverge',
                         'hemi':'rh_native_MSMAll'}),
                    '{0[id]}.R.sphere.MSMAll.native.surf.gii': {
                        'type':'registration',
                        'name':'fs_LR',
                        'tool':'MSMAll',
                        'hemi':'rh_native_MSMAll'},
                    '{0[id]}.R.sphere.MSMSulc.native.surf.gii': {
                        'type':'registration',
                        'name':'fs_LR',
                        'tool':'MSMSulc',
                        'hemi':'rh_native_MSMSulc'}}},
            'fsaverage_LR32k': {
                'type':'dir',
                'contents': {
                    '{0[id]}.L.BA.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.L.aparc.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.L.aparc.a2009s.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.L.atlasroi.32k_fs_LR.shape.gii': (
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'lh_LR32k_MSMSulc',
                         'load':_load_fsLR_atlasroi,
                         'filt':lambda x:x[0].astype(np.bool)},
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'lh_LR32k_MSMAll',
                         'load':_load_fsLR_atlasroi,
                         'filt':lambda x:x[0].astype(np.bool)}),
                    '{0[id]}.L.ArealDistortion_FS.32k_fs_LR.shape.gii': (
                        {'type':'property',
                         'name':'areal_distortion_fs',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'areal_distortion_fs',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.L.ArealDistortion_MSMSulc.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.MyelinMap.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.MyelinMap_BC.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_bc',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.SmoothedMyelinMap.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.SmoothedMyelinMap_BC.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth_bc',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.curvature.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'curvature',
                        'hemi':'lh_LR32k_MSMSulc',
                        'filt':lambda c: -c},
                    '{0[id]}.L.sulc.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'convexity',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.corrThickness.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'thickness',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.thickness.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'thickness_uncorrected',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.white.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'white',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.midthickness.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'midgray',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.pial.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'pial',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.inflated.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'inflated',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.very_inflated.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'very_inflated',
                        'hemi':'lh_LR32k_MSMSulc'},
                    '{0[id]}.L.white_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'white',
                        'hemi':'lh_LR32k_MSMAll'},
                    '{0[id]}.L.midthickness_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'midgray',
                        'hemi':'lh_LR32k_MSMAll'},
                    '{0[id]}.L.pial_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'pial',
                        'hemi':'lh_LR32k_MSMAll'},
                    '{0[id]}.L.inflated_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'inflated',
                        'hemi':'lh_LR32k_MSMAll'},
                    '{0[id]}.L.very_inflated_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'very_inflated',
                        'hemi':'lh_LR32k_MSMAll'},
                    '{0[id]}.L.flat.32k_fs_LR.surf.gii': (
                        {'type':'surface',
                         'name':'flat',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'surface',
                         'name':'flat',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.L.sphere.32k_fs_LR.surf.gii': (
                        {'type':'registration',
                         'name':'fs_LR',
                         'hemi':'lh_LR32k_MSMSulc'},
                        {'type':'registration',
                         'name':'fs_LR',
                         'hemi':'lh_LR32k_MSMAll'}),
                    '{0[id]}.R.BA.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'rh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'brodmann_area',
                         'hemi':'rh_LR32k_MSMAll'}),
                    '{0[id]}.R.aparc.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'rh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation_2005',
                         'hemi':'rh_LR32k_MSMAll'}),
                    '{0[id]}.R.aparc.a2009s.32k_fs_LR.label.gii': (
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'rh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'parcellation',
                         'hemi':'rh_LR32k_MSMAll'}),
                    '{0[id]}.R.atlasroi.32k_fs_LR.shape.gii': (
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'rh_LR32k_MSMSulc',
                         'load':_load_fsLR_atlasroi,
                         'filt':lambda x:x[1].astype(np.bool)},
                        {'type':'property',
                         'name':'atlas',
                         'hemi':'rh_LR32k_MSMAll',
                         'load':_load_fsLR_atlasroi,
                         'filt':lambda x:x[1].astype(np.bool)}),
                    '{0[id]}.R.ArealDistortion_FS.32k_fs_LR.shape.gii': (
                        {'type':'property',
                         'name':'areal_distortion_fs',
                         'hemi':'rh_LR32k_MSMSulc'},
                        {'type':'property',
                         'name':'areal_distortion_fs',
                         'hemi':'rh_LR32k_MSMAll'}),
                    '{0[id]}.R.ArealDistortion_MSMSulc.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.MyelinMap.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.MyelinMap_BC.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_bc',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.SmoothedMyelinMap.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.SmoothedMyelinMap_BC.32k_fs_LR.func.gii': {
                        'type':'property',
                        'name':'myelin_smooth_bc',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.curvature.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'curvature',
                        'hemi':'rh_LR32k_MSMSulc',
                        'filt':lambda c: -c},
                    '{0[id]}.R.sulc.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'convexity',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.corrThickness.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'thickness',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.thickness.32k_fs_LR.shape.gii': {
                        'type':'property',
                        'name':'thickness_uncorrected',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.white.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'white',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.midthickness.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'midgray',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.pial.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'pial',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.inflated.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'inflated',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.very_inflated.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'very_inflated',
                        'hemi':'rh_LR32k_MSMSulc'},
                    '{0[id]}.R.white_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'white',
                        'hemi':'rh_LR32k_MSMAll'},
                    '{0[id]}.R.midthickness_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'midgray',
                        'hemi':'rh_LR32k_MSMAll'},
                    '{0[id]}.R.pial_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'pial',
                        'hemi':'rh_LR32k_MSMAll'},
                    '{0[id]}.R.inflated_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'inflated',
                        'hemi':'rh_LR32k_MSMAll'},
                    '{0[id]}.R.very_inflated_MSMAll.32k_fs_LR.surf.gii': {
                        'type':'surface',
                        'name':'very_inflated',
                        'hemi':'rh_LR32k_MSMAll'},
                    '{0[id]}.R.flat.32k_fs_LR.surf.gii': (
                        {'type':'surface',
                         'name':'flat',
                         'hemi':'rh_LR32k_MSMSulc',
                         'load':_load_atlas_sphere},
                        {'type':'surface',
                         'name':'flat',
                         'hemi':'rh_LR32k_MSMAll',
                         'load':_load_atlas_sphere}),
                    '{0[id]}.R.sphere.32k_fs_LR.surf.gii': (
                        {'type':'registration',
                         'name':'fs_LR',
                         'hemi':'rh_LR32k_MSMSulc',
                         'load':_load_atlas_sphere},
                        {'type':'registration',
                         'name':'fs_LR',
                         'hemi':'rh_LR32k_MSMAll',
                         'load':_load_atlas_sphere}),
                    '{0[id]}.ArealDistortion_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'areal_distortion',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')},
                    '{0[id]}.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'myelin_bc',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')},
                    '{0[id]}.SmoothedMyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'myelin_smooth_bc',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')},
                    '{0[id]}.curvature_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'curvature',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll'),
                        'filt':lambda c: -c},
                    '{0[id]}.sulc_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'convexity',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')},
                    '{0[id]}.corrThickness_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'thickness',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')},
                    '{0[id]}.thickness_MSMAll.32k_fs_LR.dscalar.nii': {
                        'type':'property',
                        'name':'thickness_uncorrected',
                        'hemi':('lh_LR32k_MSMAll', 'rh_LR32k_MSMAll')}}}}}}

# Okay, convert that to something organized by hemisphere/image
def _organize_subject_directory_structure(ss):
    imgs = {}
    hems = {}
    fmap = {}
    # for walking the structure:
    def _visit(u, path, key):
        if isinstance(u, (tuple, list)):
            for uu in u: _visit(uu, path, key)
            return
        t = u['type']
        # dir is a slightly special case:
        if t == 'dir':
            newpath = os.path.join(path, key)
            for (k,v) in six.iteritems(u['contents']):
                _visit(v, newpath, k)
            return
        # Not a dir, so we can get the name and filename
        name = u['name']
        flnm = os.path.join(path, key)
        fmdat = {'filt':u.get('filt', None), 'load':u.get('load', None), 'type':t,
                 'hemi':u.get('hemi', None)}
        if t == 'image':
            if name in imgs: raise ValueError('Duplicate image in file spec: %s' % name)
            imgs[name] = flnm
            fmap[flnm] = fmdat
            return
        # not an image, so also has a hemisphere...
        h = u['hemi']
        if isinstance(h, tuple):
            hs = h
            fmdat['cifti'] = True
        else:
            hs = [h]
            fmdat['cifti'] = False
        for hh in hs:
            if hh not in hems:
                hems[hh] = {'registrations':{}, 'surfaces':{}, 'properties':{}}
        if t == 'surface':
            if name in hems[h]['surfaces']:
                raise ValueError('Duplicate surface %s in hemi %s' % (name, h))
            hems[h]['surfaces'][name] = flnm
            fmap[flnm] = fmdat
        elif t == 'registration':
            if name in hems[h]['registrations']:
                raise ValueError('Duplicate registration %s in hemi %s' % (name, h))
            hems[h]['registrations'][name] = flnm
            fmap[flnm] = fmdat
        elif t == 'property':
            if fmdat['cifti']:
                # this is a cifti file...
                fmdat['hemi'] = h
                for hh in h:
                    if name in hems[hh]['properties']:
                        raise ValueError('Duplicate property (cifti) %s in hemi %s' % (name, hh))
                    hems[hh]['properties'][name] = flnm
            else:
                # normal gifti file
                if name in hems[h]['properties']:
                    raise ValueError('Duplicate property %s in hemi %s' % (name, h))
                hems[h]['properties'][name] = flnm
            fmap[flnm] = fmdat
        else:
            raise ValueError('Unrecognized node type: %s' % t)
    # The lowest level is a directory...
    for (k,v) in six.iteritems(ss):
        _visit(v, '', k)
    # That should fix it all up!
    return {'hemis':   hems,
            'images':  imgs,
            'filemap': fmap}

subject_structure = _organize_subject_directory_structure(subject_directory_structure)


####################################################################################################
# Downloaders
# First, we can download a subject using s3fs, assuming we have the appropriate credentials.
# We can also set things up to auto-download a subject whenever they are requested but not detected.

def download(sid, credentials=None, subjects_path=None, overwrite=False, release='HCP_1200',
             database='hcp-openaccess', file_list=None):
    '''
    download(sid) downloads the data for subject with the given subject id. By default, the subject
      will be placed in the first HCP subject directory in the subjects directories list.

    Note: In order for downloading to work, you must have s3fs installed. This is not a requirement
    for the neuropythy library and does not install automatically when installing via pip. The
    github repository for this library can be found at https://github.com/dask/s3fs. Installation
    instructions can be found here: http://s3fs.readthedocs.io/en/latest/install.html

    Accepted options include:
      * credentials (default: None) may be used to specify the Amazon AWS Bucket credentials, which
        can be generated from the HCP db (https://db.humanconnectome.org/). If this argument can be
        coerced to a credentials tuple via the to_credentials function, that result will be used. If
        None, then the function will try to detect credentials via the detect_credentials function
        and will use those. If none of these work, an error is raised.
      * subjects_path (default: None) specifies where the subject should be placed. If None, then
        the first directory in the subjects paths list is used. If there is not one of these then
        an error is raised.
      * overwrite (default: False) specifies whether or not to overwrite files that already exist.
        In addition to True (do overwrite) and False (don't overwrite), the value 'error' indicates
        that an error should be raised if a file already exists.
    '''
    if s3fs is None:
        raise RuntimeError('s3fs was not successfully loaded, so downloads may not occur; check '
                           'your Python configuration to make sure that s3fs is installed. See '
                           'http://s3fs.readthedocs.io/en/latest/install.html for details.')
    if credentials is None:
        (s3fs_key, s3fs_secret) = detect_credentials()
    else:
        (s3fs_key, s3fs_secret) = to_credentials(credentials)
    if subjects_path is None:
        subjects_path = next((sd for sd in _subjects_dirs if os.path.isdir(sd)), None)
        if subjects_path is None: raise ValueError('No subjects path given or found')
    else: subjects_path = os.path.expanduser(subjects_path)
    # Make sure we can connect to the bucket first...
    fs = s3fs.S3FileSystem(key=s3fs_key, secret=s3fs_secret)
    # Okay, make sure the release is found
    if not fs.exists('/'.join([database, release])):
        raise ValueError('database/release (%s/%s) not found' % (database, release))
    # Check on the subject id to
    sid = to_subject_id(sid)
    hcp_sdir = '/'.join([database, release, str(sid)])
    if not fs.exists(hcp_sdir): raise ValueError('Subject %d not found in release' % sid)
    # Okay, lets download this subject!
    loc_sdir = os.path.join(subjects_path, str(sid))
    # walk through the subject structures
    pulled = []
    for flnm in six.iterkeys(subject_structure['filemap']):
        flnm = flnm.format({'id':sid})
        loc_flnm = os.path.join(loc_sdir, flnm)
        hcp_flnm = '/'.join([hcp_sdir, flnm])
        if not overwrite and os.path.isfile(loc_flnm): continue
        # gotta download it!
        basedir = os.path.split(loc_flnm)[0]
        if not os.path.isdir(basedir): os.makedirs(basedir, 0755)
        fs.get(hcp_flnm, loc_flnm)
        pulled.append(loc_flnm)
    return pulled

# If _auto_download_options is None, then no auto-downloading is enabled; if it is a map of
# options (even an empty one) then auto-downloading is enabled using the given options
_auto_download_options = None
def auto_download(status,
                  credentials=None, subjects_path=None, overwrite=False, release='HCP_1200',
                  database='hcp-openaccess'):
    '''
    auto_download(True) enables automatic downloading of HCP subject data when the subject ID
      is requested. The optional arguments are identical to those required for the function
      download(), and they are passed to download() when auto-downloading occurs.
    auto_download(False) disables automatic downloading.

    Automatic downloading is disabled by default unless the environment variable
    HCP_AUTO_DOWNLOAD is set to true. In this case, the database and release are derived from
    the environment variables HCP_AUTO_DATABASE and HCP_AUTO_RELEASE, and the variable
    HCP_AUTO_PATH can be used to override the default subjects path.
    '''
    global _auto_download_options
    if status:
        if s3fs is None:
            raise RuntimeError('s3fs was not successfully loaded, so downloads may not occur; check'
                               ' your Python configuration to make sure that s3fs is installed. See'
                               ' http://s3fs.readthedocs.io/en/latest/install.html for details.')
        if credentials is None:
            (s3fs_key, s3fs_secret) = detect_credentials()
        else:
            (s3fs_key, s3fs_secret) = to_credentials(credentials)
        if subjects_path is None:
            subjects_path = next((sd for sd in _subjects_dirs if os.path.isdir(sd)), None)
            if subjects_path is None: raise ValueError('No subjects path given or found')
        else: subjects_path = os.path.expanduser(subjects_path)
        fs = s3fs.S3FileSystem(key=s3fs_key, secret=s3fs_secret)
        hcpbase = '/'.join([database, release])
        if not fs.exists(hcpbase):
            raise ValueError('database/release (%s/%s) not found' % (database, release))
        sids = set([])
        for f in fs.ls(hcpbase):
            f = os.path.split(f)[-1]
            if len(f) == 6 and f[0] != '0':
                try: sids.add(int(f))
                except: pass
        _auto_download_options = dict(
            subjects_path=subjects_path,
            overwrite=overwrite,
            release=release,
            database=database,
            subject_ids=frozenset(sids),
            s3fs=fs)
    else:
        _auto_download_options = None
# See if the environment lets auto-downloading start out on
if 'HCP_AUTO_DOWNLOAD' in os.environ and \
   os.environ['HCP_AUTO_DOWNLOAD'].lower() in ('on', 'yes', 'true', '1'):
    args = {}
    if 'HCP_AUTO_RELEASE'  in os.environ: args['release']  = os.environ['HCP_AUTO_RELEASE']
    if 'HCP_AUTO_DATABASE' in os.environ: args['database'] = os.environ['HCP_AUTO_DATABASE']
    if 'HCP_AUTO_PATH'     in os.environ: args['subjects_path'] = os.environ['HCP_AUTO_PATH']
    try: auto_download(True, **args)
    except: pass
def _auto_downloadable(sid):
    if _auto_download_options is None: return False
    sid = to_subject_id(sid)
    return sid in _auto_download_options['subject_ids']

def subject_filemap(sid, subject_path=None):
    '''
    subject_filemap(sid) yields a persistent lazy map structure that loads the relevant files as
      requested for the given subject. The sid may be a subject id or the path of a subject
      directory. If a subject id is given, then the subject is searched for in the known subject
      paths.

    The optional argument subject_path may be set to a specific path to ensure that the subject
    id is only searched for in the given path.
    '''
    # see if sid is a subject id
    if pimms.is_int(sid):
        if subject_path is None: sdir = find_subject_path(sid)
        else: sdir = os.path.expanduser(os.path.join(subject_path, str(sid)))
    elif pimms.is_str(sid):
        try: sid = os.path.expanduser(sid)
        except: pass
        sdir = sid if os.path.isdir(sid) else find_subject_path(sid)
        sid  = int(sdir.split(os.sep)[-1])
    else: raise ValueError('Cannot understand HCP subject ID %s' % sid)
    if sdir is None:
        if _auto_download_options is not None and _auto_downloadable(sid):
            # we didn't find it, but we have a place to put it
            sdir = _auto_download_options['subjects_path']
            sdir = os.path.join(sdir, str(sid))
            if not os.path.isdir(sdir): os.makedirs(sdir, 0755)
        else:
            raise ValueError('Could not find HCP subject %s' % sid)
    ff = {'id':sid}
    def _make_lambda(flnm, dat): return lambda:_load(flnm, dat)
    # walk through the subject structure's filemap to make a lazy map that loads things
    dats = {}
    fls = {}
    for (k,v) in six.iteritems(subject_structure['filemap']):
        flnm = os.path.join(sdir, k.format(ff))
        dat  = pimms.merge(v, ff)
        fls[flnm]  = _make_lambda(flnm, dat)
        dats[flnm] = dat
    fls = pimms.lazy_map(fls)
    # and the the hemispheres to make hemispheres...
    def _lookup_fl(flnm):
        def _f():
            obj = fls[flnm]
            dat = dats[flnm]
            if 'cifti' in dat and dat['cifti']:
                # we need to handle the cifti files by splitting them up according to atlases
                (la, ra) = _load_fsLR_atlasroi(flnm, dat)
                (ln, rn) = [aa.shape[0]     for aa in (la, ra)]
                (li, ri) = [np.where(aa)[0] for aa in (la, ra)]
                (lu, ru) = [len(ai)         for ai in (li, ri)]
                if dat['hemi'][0:2] == 'lh': (ii,jj,nn) = (slice(0,  lu),      li, ln)
                else:                        (ii,jj,nn) = (slice(lu, lu + ru), ri, rn)
                cu = lu + ru # number of slots in a cifti file
                tmp = np.asarray(obj)
                if tmp.shape[0] < cu: tmp = tmp.T
                if tmp.shape[0] < cu: raise ValueError('no matching size in cifti file')
                obj = np.zeros((nn,) + tmp.shape[1:], dtype=tmp.dtype)
                obj[jj] = tmp[ii]
                obj.setflags(write=False)
            return obj
        return _f
    hems = pyr.pmap(
        {h: pyr.pmap(
            {k: pimms.lazy_map({nm: _lookup_fl(fnm)
                                for (nm,fl) in six.iteritems(v)
                                for fnm in [os.path.join(sdir, fl.format(ff))]})
             for (k,v) in six.iteritems(entries)})
         for (h,entries) in six.iteritems(subject_structure['hemis'])})
    # and the images
    imgs = pimms.lazy_map({k: _lookup_fl(os.path.join(sdir, v.format(ff)))
                           for (k,v) in six.iteritems(subject_structure['images'])})
    return pyr.pmap({'images': imgs, 'hemis': hems})