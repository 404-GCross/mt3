Name: mt3-transcriber
Version: %{?version}%{!?version:0.1.0}
Release: 1%{?dist}
Summary: MT3 music transcription desktop application
License: Apache-2.0
BuildArch: x86_64
Requires: git, curl, libsndfile, tcl, tk
Source0: mt3-transcriber.tar.gz

%description
Audio to MIDI transcription with MT3 piano and multi-instrument models.

%prep
%setup -q -n mt3-transcriber

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}%{_prefix}/share/mt3-transcriber
mkdir -p %{buildroot}%{_bindir} %{buildroot}%{_datadir}/applications
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
cp -a mt3 gui packaging %{buildroot}%{_prefix}/share/mt3-transcriber/
cat > %{buildroot}%{_bindir}/mt3-transcriber <<'EOF'
#!/usr/bin/env bash
exec /usr/share/mt3-transcriber/gui/run.sh "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/mt3-transcriber %{buildroot}%{_prefix}/share/mt3-transcriber/gui/*.sh
cp %{buildroot}%{_prefix}/share/mt3-transcriber/packaging/mt3-transcriber.desktop \
  %{buildroot}%{_datadir}/applications/
cp %{buildroot}%{_prefix}/share/mt3-transcriber/packaging/mt3-transcriber.png \
  %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/

%files
%{_bindir}/mt3-transcriber
%{_prefix}/share/mt3-transcriber
%{_datadir}/applications/mt3-transcriber.desktop
%{_datadir}/icons/hicolor/256x256/apps/mt3-transcriber.png

%post
echo "MT3 Transcriber 已安装。首次使用请运行: mt3-transcriber"
