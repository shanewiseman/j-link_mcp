resource "aws_security_group" "jenkins_gate_probe" {
  name = "jenkins-gate-probe"

  ingress {
    description = "Intentional CI-only world-open SSH rule"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
