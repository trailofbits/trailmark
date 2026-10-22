class ApplicationController
end

class UsersController < ApplicationController
  def show
    helper()
  end

  private
  def helper
    "secret helper"
  end

  protected
  def internal_token
    "protected helper"
  end

  def self.rebuild_index
    "class helper"
  end
end

class EmailWorker
  def initialize
    @ready = true
  end
  include Sidekiq::Job
  def perform(user_id)
    puts user_id
  end
end
