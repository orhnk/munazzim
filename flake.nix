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
      in {
        packages.default = munazzimApp;

        apps.default = {
          type = "app";
          program = "${munazzimApp}/bin/munazzim";
        };

        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: (with ps; [ textual rich httpx pytest black google-api-python-client google-auth google-auth-oauthlib ]) ++ [ islamPkg ]))
          ];
        };
      }
    );
}
