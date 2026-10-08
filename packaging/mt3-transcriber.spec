Name: mt3-transcriber
Version: %{?version}%{!?version:0.1.0}
Release: 1%{?dist}
Summary: MT3 music transcription desktop application
License: Apache-2.0
BuildArch: x86_64
Requires: python3 >= 3.12, python3-tkinter, python3-venv, libsndfile, git

%description
Audio to MIDI transcription with MT3 piano and multi-instrument models.

%prep
%setup -q -n mt3-transcriber

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}%{_prefix}/share/mt3-transcriber
mkdir -p %{buildroot}%{_bindir} %{buildroot}%{_datadir}/applications
cp -a mt3 gui packaging %{buildroot}%{_prefix}/share/mt3-transcriber/
cat > %{buildroot}%{_bindir}/mt3-transcriber <<'EOF'
#!/usr/bin/env bash
exec /usr/share/mt3-transcriber/gui/run.sh "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/mt3-transcriber %{buildroot}%{_prefix}/share/mt3-transcriber/gui/*.sh
cp %{buildroot}%{_prefix}/share/mt3-transcriber/packaging/mt3-transcriber.desktop \
  %{buildroot}%{_datadir}/applications/

%files
%{_bindir}/mt3-transcriber
%{_prefix}/share/mt3-transcriber
%{_datadir}/applications/mt3-transcriber.desktop

%post
echo "MT3 Transcriber 已安装。首次使用请运行: mt3-transcriber"
