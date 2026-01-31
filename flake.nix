{
  description = "Munazzim daily planner";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python311;
        pythonPackages = pkgs.python311Packages;
        islamPkg = pythonPackages.buildPythonPackage {
          pname = "islam";
          version = "2.2.0";
          format = "setuptools";
          src = pkgs.fetchPypi {
            pname = "islam";
            version = "2.2.0";
            sha256 = "0k001vdz7a29d4136iryvva167zhdsqnznbk15wa1b2chfcyfmax";
          };
          doCheck = false;
        };
        munazzimApp = pythonPackages.buildPythonApplication {
          pname = "munazzim";
          version = "0.1.0";
          format = "pyproject";
          src = pkgs.lib.cleanSource self;
          nativeBuildInputs = with pythonPackages; [ setuptools wheel ];
          propagatedBuildInputs = with pythonPackages; [ textual rich httpx google-api-python-client google-auth google-auth-oauthlib ] ++ [ islamPkg ];
        };
        munazzimLaunch = pkgs.writeShellApplication {
          name = "munazzim-launch";
          text = ''
            set -euo pipefail

            detect_terminal() {
              if [ -n "''${TERMINAL-}" ] && command -v "''${TERMINAL}" >/dev/null 2>&1; then
                echo "''${TERMINAL}"
                return 0
              fi

              for term in x-terminal-emulator gnome-terminal konsole alacritty kitty foot wezterm xterm; do
                if command -v "''${term}" >/dev/null 2>&1; then
                  echo "''${term}"
                  return 0
                fi
              done

              return 1
            }

            run_munazzim() {
              exec "${munazzimApp}/bin/munazzim"
            }

            if term="$(detect_terminal)"; then
              case "''${term}" in
                gnome-terminal)
                  exec "''${term}" -- "${munazzimApp}/bin/munazzim"
                  ;;
                konsole)
                  exec "''${term}" -e "${munazzimApp}/bin/munazzim"
                  ;;
                wezterm)
                  exec "''${term}" start -- "${munazzimApp}/bin/munazzim"
                  ;;
                *)
                  exec "''${term}" -e "${munazzimApp}/bin/munazzim"
                  ;;
              esac
            else
              run_munazzim
            fi
          '';
        };
        munazzimDesktopItem = pkgs.makeDesktopItem {
          name = "munazzim";
          desktopName = "Munazzim";
          exec = "${munazzimLaunch}/bin/munazzim-launch";
          terminal = false;
          comment = "Munazzim daily planner";
          categories = [ "Office" "Utility" ];
        };
        munazzimDesktop = pkgs.symlinkJoin {
          name = "munazzim";
          paths = [ munazzimApp munazzimDesktopItem ];
        };
      in {
        packages.default = munazzimDesktop;
        packages.munazzim = munazzimApp;
        packages.desktop = munazzimDesktop;

        apps.default = {
          type = "app";
          program = "${munazzimApp}/bin/munazzim";
        };
        apps.desktop = {
          type = "app";
          program = "${munazzimLaunch}/bin/munazzim-launch";
        };

        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: (with ps; [ textual rich httpx pytest black google-api-python-client google-auth google-auth-oauthlib ]) ++ [ islamPkg ]))
          ];
        };
      }
    );
}
