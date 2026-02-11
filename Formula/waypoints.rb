class Waypoints < Formula
  include Language::Python::Virtualenv

  desc "AI-native software development environment"
  homepage "https://github.com/kulesh/waypoints"
  # Temporary commit-pinned source archive until first semver release tag.
  url "https://github.com/kulesh/waypoints/archive/29b44172b8f8a45fffc92f4c86c07ddf3e38bc4f.tar.gz"
  version "0.1.0"
  sha256 "4594bbc3af3e1896f75431d0e79c3837ca5fef59f5cf7170d6f1b134972f9351"
  license "MIT"

  depends_on "rust" => :build
  depends_on "python@3.14"

  def install
    python = Formula["python@3.14"].opt_bin/"python3.14"
    ENV["CARGO_PROFILE_RELEASE_LTO"] = "off"
    ENV["CARGO_PROFILE_RELEASE_CODEGEN_UNITS"] = "16"
    ENV.append "RUSTFLAGS", " -C link-arg=-Wl,-headerpad_max_install_names"
    virtualenv_create(libexec, "python3.14")
    system python, "-m", "pip", "--python=#{libexec}/bin/python", "install", "--upgrade", "pip"
    pip_args = [
      "-m",
      "pip",
      "--python=#{libexec}/bin/python",
      "install",
      "--no-cache-dir",
      "--force-reinstall",
      "--no-binary=cryptography,jiter,pydantic-core,rpds-py",
      buildpath.to_s,
    ]
    system python, *pip_args
    bin.install_symlink libexec/"bin/waypoints"
  end

  test do
    assert_match "usage:", shell_output("#{bin}/waypoints --help")
  end
end
