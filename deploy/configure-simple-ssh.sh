#!/usr/bin/env bash
set -euo pipefail

readonly SSHD_CONFIG=/etc/ssh/sshd_config
readonly SSHD=/usr/sbin/sshd
readonly ENTRYPOINT=/opt/simple/deploy/simple-ssh-entrypoint.sh
readonly MARKER="# SIMPLE restricted CLI access - managed block"

fail() {
  printf 'Erro: %s\n' "$*" >&2
  exit 1
}

if ((EUID != 0)); then
  fail "execute este procedimento como root."
fi

[[ -f "$SSHD_CONFIG" && ! -L "$SSHD_CONFIG" ]] ||
  fail "configuração SSH ausente ou inválida em ${SSHD_CONFIG}."
[[ -x "$SSHD" ]] || fail "sshd não está disponível em ${SSHD}."
[[ -x "$ENTRYPOINT" ]] || fail "launcher SIMPLE ausente ou não executável."
getent group simple-users >/dev/null ||
  fail "o grupo simple-users não existe."
id -nG simple-broker | tr ' ' '\n' | grep -Fxq simple-users ||
  fail "simple-broker precisa pertencer ao grupo simple-users."
if grep -Fq "$MARKER" "$SSHD_CONFIG"; then
  fail "a configuração restrita do SIMPLE já foi instalada."
fi

backup="${SSHD_CONFIG}.simple-backup-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$backup" && ! -L "$backup" ]] ||
  fail "o arquivo de backup já existe: ${backup}."
temporary=$(mktemp /etc/ssh/sshd_config.simple.XXXXXX)
trap 'rm -f -- "$temporary"' EXIT

cp -a -- "$SSHD_CONFIG" "$backup"
cat -- "$SSHD_CONFIG" >"$temporary"
cat >>"$temporary" <<'EOF'

# SIMPLE restricted CLI access - managed block
Match Group simple-users
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
    ForceCommand /opt/simple/deploy/simple-ssh-entrypoint.sh
    PermitTTY yes
    DisableForwarding yes
    AllowTcpForwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitUserRC no
    PermitUserEnvironment no
EOF
chown --reference="$SSHD_CONFIG" "$temporary"
chmod --reference="$SSHD_CONFIG" "$temporary"

if ! "$SSHD" -t -f "$temporary"; then
  fail "sshd recusou a configuração candidata; a configuração ativa não foi alterada."
fi

if ! mv -- "$temporary" "$SSHD_CONFIG"; then
  fail "não foi possível instalar a configuração SSH."
fi

restore_config() {
  cp -a -- "$backup" "$SSHD_CONFIG"
}

if ! "$SSHD" -t -f "$SSHD_CONFIG"; then
  restore_config
  fail "a configuração final falhou na validação; o arquivo anterior foi restaurado."
fi

effective=$("$SSHD" -T -f "$SSHD_CONFIG" \
  -C user=simple-broker,host=localhost,addr=127.0.0.1) ||
  {
    restore_config
    fail "não foi possível verificar as opções efetivas; o arquivo anterior foi restaurado."
  }

expected=(
  "authenticationmethods publickey"
  "passwordauthentication no"
  "kbdinteractiveauthentication no"
  "pubkeyauthentication yes"
  "forcecommand /opt/simple/deploy/simple-ssh-entrypoint.sh"
  "permittty yes"
  "disableforwarding yes"
  "allowtcpforwarding no"
  "allowagentforwarding no"
  "allowstreamlocalforwarding no"
  "x11forwarding no"
  "permittunnel no"
  "permituserrc no"
  "permituserenvironment no"
)
for setting in "${expected[@]}"; do
  if ! grep -Fxq "$setting" <<<"$effective"; then
    restore_config
    fail "opção efetiva inesperada (${setting}); o arquivo anterior foi restaurado."
  fi
done

printf 'Configuração SSH SIMPLE instalada e validada.\n'
printf 'Backup: %s\n' "$backup"
printf 'O SSH não foi recarregado. Revise as opções efetivas antes de executar: systemctl reload ssh\n'
