project('com.github.hezral.clips',  
          version: '1.2.2',
    meson_version: '>= 0.49.0',
  default_options: [ 'warning_level=2',
                   ],
)

i18n = import('i18n')


subdir('data')
subdir('src')
subdir('po')

test_python = find_program('python3')
test_dbus = find_program('dbus-run-session')
test('Paste backends', test_dbus,
  args: ['--', test_python.full_path(), '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
  workdir: meson.project_source_root(),
  timeout: 120,
)

meson.add_install_script('build-aux/meson/postinstall.py')
