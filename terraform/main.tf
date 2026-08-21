provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3  = "http://localhost:4566"
    ec2 = "http://localhost:4566"
    iam = "http://localhost:4566"
  }
}

resource "aws_s3_bucket" "demo_bucket" {
  bucket = "sumit-demo-bucket"
}

resource "aws_instance" "demo_server" {
  ami           = "ami-12345678"
  instance_type = "t2.micro"
}

resource "aws_iam_user" "demo_user" {
  name = "sumit-demo-user"
}
